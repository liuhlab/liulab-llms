# GCP with SkyPilot

The current deployment. Run every `sky` command from `deploy/gcp/`, beside the task file and
its `.sky.yaml`. Placeholders: `<project>`, `<region>` and `<zone>` (the task file's
`infra`), `<bucket>`, and `<instance>`, the VM's name:

```bash
gcloud compute instances list --filter='name~^llm-gateway-' --format='value(name)'
```

## First setup

1. **SkyPilot 0.14.2 as a pixi global tool**, with protobuf held below 7, which its GCP
   support needs:

   ```bash
   pixi global install skypilot --with google-api-python-client --with google-cloud-storage --with pyopenssl --with grpcio
   pixi global add --environment skypilot "protobuf>=5.29.6,<7"
   ```

2. **Two `gcloud` logins, in this order**, both in a browser, so the project becomes the
   quota project. SkyPilot reads the second.

   ```bash
   gcloud auth login
   gcloud config set project <project>
   gcloud auth application-default login
   ```

3. `sky check gcp` passes.
4. **The local SkyPilot patch.** The organization denies `setIamPolicy`, so SkyPilot's first
   launch fails granting its service account `roles/iam.roleViewer`. In
   `~/.pixi/envs/skypilot/lib/python3.*/site-packages/sky/provision/gcp/constants.py`,
   comment out `'roles/iam.roleViewer',` in `DEFAULT_SERVICE_ACCOUNT_ROLES`. `pixi global
   update` overwrites it: reapply it after every update.
5. **The data disk, its snapshot schedule and the backup bucket.** The disk exists before
   the launch, so SkyPilot attaches it rather than creating one at its costliest tier.

   ```bash
   gcloud compute disks create llm-gateway-data --zone=<zone> --size=20GB --type=pd-balanced
   gcloud compute resource-policies create snapshot-schedule llm-gateway-daily --region=<region> \
     --daily-schedule --start-time=09:00 --max-retention-days=14 \
     --on-source-disk-delete=keep-auto-snapshots --storage-location=<region>
   gcloud compute disks add-resource-policies llm-gateway-data --zone=<zone> --resource-policies=llm-gateway-daily
   gcloud storage buckets create gs://<bucket> --location=<region> --uniform-bucket-level-access --public-access-prevention
   gcloud storage buckets update gs://<bucket> --lifecycle-file=backup-lifecycle.json
   ```

6. **Launch:** `sky launch -c llm-gateway llm-gateway.yaml -y --env BACKUP_BUCKET=gs://<bucket>`.
   The setup runs as job 1; it ends `Job finished (status: SUCCEEDED)`, else read
   `sky logs llm-gateway 1`. With no keys yet, the gateway waits stopped.
7. **Fix the address**, promoting the VM's current one:

   ```bash
   gcloud compute addresses create llm-gateway-ip --region=<region> \
     --addresses="$(gcloud compute instances describe <instance> --zone=<zone> --format='value(networkInterfaces[0].accessConfigs[0].natIP)')"
   ```

8. **Only SSH open.** The default network opens more than SSH, such as RDP. Leave the
   project's rules alone. Tag the VM, and add one rule that denies the tag everything but
   SSH (`<network>` is the VM's, `default` unless set):

   ```bash
   gcloud compute instances add-tags <instance> --zone=<zone> --tags=llm-gateway
   gcloud compute firewall-rules create llm-gateway-only-ssh --network=<network> \
     --direction=INGRESS --action=DENY --rules=tcp:0-21,tcp:23-65535,udp,icmp \
     --source-ranges=0.0.0.0/0 --target-tags=llm-gateway --priority=1000
   ```

   SkyPilot has no field for a network tag, so a new VM needs `add-tags` again.

9. **Cut the access scopes** to Cloud Storage read and write, which the nightly dump needs:
   see "Cut the scopes".
10. **Provider keys**, then "Check it works", both in [SKILL.md](SKILL.md).
11. **Memory.** After the check's real call, run `ssh llm-gateway free -m`. If under a quarter
    of the memory is available, or swap is in use at rest, set `instance_type: e2-medium` and
    rebuild.

## Cut the scopes

SkyPilot sets the `cloud-platform` scope only when it creates a VM. A rerun of `sky launch`
keeps the cut; a new VM needs it again.

```bash
sky stop llm-gateway -y
gcloud compute instances set-service-account <instance> --zone=<zone> \
  --service-account="$(gcloud compute instances describe <instance> --zone=<zone> --format='value(serviceAccounts[0].email)')" \
  --scopes=storage-rw
sky start llm-gateway -y
```

## Rebuild

After `sky down`, or to change the machine type: launch (step 6), swap the fixed address
onto the new VM, tag it (step 8's `add-tags`), cut the scopes, send the provider keys, check.
The data disk reattaches by name.

```bash
gcloud compute instances delete-access-config <instance> --zone=<zone> --access-config-name="External NAT"
gcloud compute instances add-access-config <instance> --zone=<zone> --access-config-name="External NAT" \
  --address="$(gcloud compute addresses describe llm-gateway-ip --region=<region> --format='value(address)')"
```

## Snapshot before an upgrade

```bash
gcloud compute snapshots create llm-gateway-before-<tag> --source-disk=llm-gateway-data --source-disk-zone=<zone>
```

## Restore from a snapshot

Use the dump ([SKILL.md](SKILL.md)) first; a snapshot is the whole disk. To look inside one
without touching the data disk, make a disk from it, attach it with
`--device-name=restore-test`, and mount `/dev/disk/by-id/google-restore-test`. Delete the
copy's `pgdata/postmaster.pid`, which names the live server's process. Start a second server
on it with the environment's `pg_ctl`, with `-o "-c listen_addresses= -c port=5433 -c
unix_socket_directories=<dir>"`. Afterwards stop it, then detach and delete the copy.

To restore, snapshot the disk as it is, then swap in a disk made from the chosen snapshot
under the same name:

```bash
gcloud compute snapshots list --filter='sourceDisk~llm-gateway-data'
ssh llm-gateway 'sudo systemctl stop labllm-gateway labllm-postgres && sudo umount /mnt/data'
gcloud compute instances detach-disk <instance> --zone=<zone> --disk=llm-gateway-data
gcloud compute disks delete llm-gateway-data --zone=<zone>
gcloud compute disks create llm-gateway-data --zone=<zone> --type=pd-balanced --source-snapshot=<snapshot>
gcloud compute disks add-resource-policies llm-gateway-data --zone=<zone> --resource-policies=llm-gateway-daily
gcloud compute instances attach-disk <instance> --zone=<zone> --disk=llm-gateway-data --device-name=sky-disk-0
ssh llm-gateway 'sudo mount /mnt/data && sudo systemctl start labllm-postgres labllm-gateway'
```

PostgreSQL replays its log on the restored copy at start, as after a crash.

## Teardown

`sky down llm-gateway` deletes the VM and its boot disk, keys included. The data disk, its
snapshots, the bucket and the fixed address stay, and are billed, until each is deleted with
`gcloud`.
