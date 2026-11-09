# Running a small always-on service on GCP with SkyPilot

Query date: 2026-11-08. This note answers the seven points of the research ticket from primary
sources only:

- **SkyPilot** at release `v0.14.2` (published 2026-10-28, the newest release on the query date),
  commit
  [`39b151e`](https://github.com/skypilot-org/skypilot/tree/39b151e11075f1d0029f8fc411a42334775fb3e7).
  SkyPilot's docs, published at <https://docs.skypilot.co>, are built from `docs/source/` in that
  repository. Every SkyPilot link below points at that commit, with line numbers.
- **SkyPilot's hosted machine catalog**, `skypilot-org/skypilot-catalog` at commit
  [`aaa3d35`](https://github.com/skypilot-org/skypilot-catalog/blob/aaa3d35142a01c66b04f96b0d34e5933c6f1394c/catalogs/v8/gcp/vms.csv),
  only to check which GCP machine types SkyPilot knows.
- **GCP's docs** (docs.cloud.google.com) and **GCP's pricing pages** (cloud.google.com).
- **Each model provider's own page** on where its service may be used.
- **Caddy's docs**, once, for automatic HTTPS.

Nothing was installed or run. No cloud account was used. SkyPilot's source was read from a
downloaded archive.

Each claim is marked **read** (seen in the source cited), **inferred** (follows from what was read,
not seen directly) or **not established**.

**Prices change.** Every price below was read on 2026-11-08 from the GCP page named, for
`us-central1` (Iowa), in US dollars, before any discount. A monthly figure is the hourly price times
730 hours; that multiplication is mine.

Terms used below: a **cluster** is SkyPilot's name for what `sky launch` creates, even when it is one
VM. **SkyServe** is SkyPilot's serving layer, started with `sky serve up`. A **persistent disk** is
GCP's network disk that a VM mounts like a local one. **Cloud SQL** is GCP's managed PostgreSQL. A
**service account** is a GCP identity for a program rather than a person.

## Answer in brief

- **Plain `sky launch` fits one always-on service; SkyServe does not.** SkyServe puts a controller
  VM of its own (by default 4+ vCPUs, 8+ GB) in front of the service VM, and its docs call it beta
  and "not recommended for external/production serving". SkyPilot's own docs run their API server,
  an always-on HTTP service, with plain `sky launch`.
- **The plain route costs about $21 a month** for an `e2-small` VM, a 30 GB boot disk, a 20 GB data
  disk and a fixed IP. SkyServe adds at least its controller; an `e2-standard-4` alone lists at $98 a
  month.
- **Staying up is mostly GCP's job.** A launch never autostops unless asked. GCP live-migrates a
  standard VM during maintenance and restarts it after a host crash, and SkyPilot overrides neither
  for an on-demand CPU VM. Nothing read restarts the service process after a reboot, so the service
  should run under systemd, installed by the YAML's `setup`. A deleted VM is recreated by running
  `sky launch` again.
- **SkyPilot gives no fixed address.** The VM gets an ephemeral IP, which GCP releases when the VM
  stops. Fixing it takes `gcloud` commands outside SkyPilot. SkyPilot opens ports to the whole
  internet and does nothing for HTTPS or DNS; a reverse proxy with automatic certificates on ports
  80 and 443, plus one DNS record, covers both.
- **PostgreSQL's data.** SkyPilot's documented volumes do not cover GCP VMs. An undocumented
  `resources.volumes` field attaches a named persistent disk that survives `sky down`. Cloud SQL's
  smallest instance is $7.67 a month plus storage, outside Cloud SQL's SLA, and SkyPilot does not
  manage Cloud SQL at all.
- **Secrets.** SkyPilot's `secrets` field only hides values in its dashboard; they reach the VM as
  plain environment variables. GCP Secret Manager is free at this scale and is read by the VM's own
  service account. By default SkyPilot copies the launching person's own GCP credentials onto the VM.
- **Regions.** Every GCP region except Hong Kong (`asia-east2`) is in a country that both OpenAI and
  Anthropic list. DeepSeek and TypeSafe publish no country list; their terms exclude only sanctioned
  or embargoed countries.

## 1. Plain `sky launch` or SkyServe

### What each one adds

- **Read:** SkyServe "is currently in **beta**. It is well-suited for internal serving use cases
  (R&D, batch inference) but is not recommended for external/production serving"
  ([`serving/sky-serve.rst` L21–23](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/serving/sky-serve.rst#L21-L23)).
- **Read:** SkyServe runs one centralized controller VM holding a *controller*, which watches the
  replicas, relaunches a failed one and autoscales, and a *load balancer*, an HTTP server on the
  service endpoint that routes each request to a ready replica
  ([`sky-serve.rst` L479–486](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/serving/sky-serve.rst#L479-L486)).
  The controller is "a small on-demand CPU VM", launched with the first service and autostopped 10
  minutes after the last service ends
  ([L491–498](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/serving/sky-serve.rst#L491-L498)).
- **Read:** the controller's default resources are `{'cpus': '4+', 'memory': '8+'}`; its ports start
  at 20001 and the load balancer's endpoint takes a port in 30001–30020
  ([`sky/serve/constants.py` L87, L102–104](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/sky/serve/constants.py#L87-L104)).
  The controller's size, location and disk (default 200 GB per the docs) can be set under
  `serve.controller.resources` in `~/.sky/config.yaml`, but only before the controller first exists
  ([`sky-serve.rst` L511–559](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/serving/sky-serve.rst#L511-L559)).
- **Read:** a highly available controller exists only when the controller runs on Kubernetes
  ([`sky-serve.rst` L502–507](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/serving/sky-serve.rst#L502-L507);
  [`serving/serve-high-availability.rst` L7–20](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/serving/serve-high-availability.rst#L7-L20)).
- **Read:** a SkyServe update is rolling by default: new replicas come up before old ones are
  removed, and the endpoint stays the same
  ([`serving/update.rst` L12–14, L27–32](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/serving/update.rst#L12-L32)).
- **Read:** SkyPilot's own recipe for its API server on a cloud VM is a plain `sky launch` with
  `resources.ports` and a `run` command that starts the server; `sky status --endpoint 46580
  api-server` prints its URL. The recipe warns that "VM deployment does not offer failover and
  graceful upgrading supports"
  ([`reference/api-server/api-server-admin-deploy.rst` L1656–1713](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/reference/api-server/api-server-admin-deploy.rst#L1656-L1713)).
- **Read:** SkyPilot's serving examples put SkyServe in front of OpenAI-compatible model servers
  (vLLM, TGI, FastChat)
  ([`sky-serve.rst` L56–108, L291–364](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/serving/sky-serve.rst#L56-L364)).
  No doc or example at this commit runs LiteLLM's proxy: a search of the repository for "litellm"
  finds only a training example's dependencies under `llm/slime/`.

### What each costs to keep running

| Item (us-central1) | Plain `sky launch` | SkyServe |
| --- | --- | --- |
| Service VM, `e2-small` (2 shared vCPUs, 2 GB) | $0.0168/h, $12.23/month | same (the "replica") |
| Controller VM, 4+ vCPUs and 8+ GB | none | from $0.134/h, $97.84/month, if `e2-standard-4` |
| Boot disks, `pd-balanced` | 30 GB: $3.00/month | plus the controller's 200 GB: $20/month |
| Data disk, `pd-balanced`, 20 GB | $2.00/month | same, but see the conflict below |
| External IP on a running VM | $3.65/month | one per VM |

- **Read, prices:** `e2-micro` $0.008376428/h, `e2-small` $0.016752855/h, `e2-medium` $0.03350571/h,
  `e2-standard-2` $0.06701142/h, `e2-standard-4` $0.13402284/h
  ([General-purpose VM pricing](https://cloud.google.com/products/compute/pricing/general-purpose));
  balanced persistent disk $0.000136986 per GiB-hour, about $0.10 per GiB-month
  ([Disk and image pricing](https://cloud.google.com/compute/disks-image-pricing)); an external IP in
  use on a standard VM $0.005/h ([Network pricing](https://cloud.google.com/vpc/network-pricing)).
- **Read:** GCP's free tier covers one `e2-micro` VM a month in `us-west1`, `us-central1` or
  `us-east1`, and 30 GB-months of standard persistent disk
  ([Free cloud features](https://docs.cloud.google.com/free/docs/free-cloud-features)).
- **Not established:** which machine type SkyPilot picks on GCP for the controller's "4+ vCPUs, 8+
  GB". `e2-standard-4` is used above only as a price point.
- **Read:** with nothing specified, SkyPilot launches an 8+ vCPU VM with 4 GB per vCPU from the
  `n2`/`n4` families
  ([`sky/catalog/gcp_catalog.py` L41–79](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/sky/catalog/gcp_catalog.py#L41-L79)),
  with a 256 GB boot disk
  ([`sky/resources.py` L42](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/sky/resources.py#L42))
  on the "medium" tier, which is `pd-balanced` on GCP
  ([`reference/yaml-spec.rst` L554–560](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/reference/yaml-spec.rst#L554-L560);
  [`sky/clouds/gcp.py` L1224–1229](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/sky/clouds/gcp.py#L1224-L1229)).
  **Inferred:** the gateway's YAML must name `instance_type` and `disk_size`, or the VM is far
  larger than needed.
- **Read:** SkyPilot's catalog lists `e2-micro`, `e2-small` and `e2-medium` in `us-central1`, at
  prices that do not match GCP's page (for example $0.04655/h for `e2-micro`)
  ([`vms.csv`](https://github.com/skypilot-org/skypilot-catalog/blob/aaa3d35142a01c66b04f96b0d34e5933c6f1394c/catalogs/v8/gcp/vms.csv)).
  **Inferred:** SkyPilot can launch E2 types, but its cost estimate for them is wrong; trust GCP's
  page.

### Which suits one always-on service

**Inferred:** plain `sky launch`. With one replica there is nothing to balance or autoscale, so
SkyServe's additions come down to relaunching a failed replica, at the price of a second, larger VM
and a beta label. SkyServe also clashes with a database on the replica. Its rolling update and its
relaunch both bring up a new replica VM while the old one exists. A balanced persistent disk cannot
be written by two VMs at once: GCP's page says "Multi-writer mode is only supported for SSD type
Persistent Disk volumes"
([Share a disk between VMs](https://docs.cloud.google.com/compute/docs/disks/sharing-disks-between-vms)).
SkyServe fits only if PostgreSQL lives off the VM, in Cloud SQL.

## 2. Staying up

- **Read:** autostop is opt-in, set by `resources.autostop` in the YAML, `sky launch -i` or
  `sky autostop`, and cancelled with `sky autostop CLUSTER --cancel`
  ([`reference/auto-stop.rst` L21–87](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/reference/auto-stop.rst#L21-L87);
  [`yaml-spec.rst` L256–280](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/reference/yaml-spec.rst#L256-L280)).
  **Inferred:** a launch that sets none keeps running.
- **Read:** on GCP, most VMs live-migrate during host maintenance, automatic restart is the default,
  and after a host error Compute Engine restarts the VM "typically within three minutes"
  ([Host events overview](https://docs.cloud.google.com/compute/docs/instances/host-maintenance-overview)).
- **Read:** SkyPilot's GCP template writes a `scheduling` block only for spot VMs or VMs with GPUs
  ([`sky/templates/gcp-ray.yml.j2` L215–227](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/sky/templates/gcp-ray.yml.j2#L215-L227)).
  **Inferred:** an on-demand CPU gateway VM keeps GCP's defaults: live migration and automatic
  restart.
- **Not established:** what SkyPilot does with the `run` job after GCP restarts the VM. `sky start`
  restarts a stopped cluster in the same zone and its help says nothing about rerunning jobs
  ([`sky/client/cli/command.py` L3304–3315](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/sky/client/cli/command.py#L3304-L3315)).
  **Inferred:** the gateway, PostgreSQL and the reverse proxy should be systemd units enabled at
  boot, written by `setup`, so a reboot brings them back without SkyPilot.
- **Read:** SkyPilot's managed jobs (`sky jobs launch`) relaunch a failed job, by default in the next
  region (`EAGER_NEXT_REGION`)
  ([`yaml-spec.rst` L949–961](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/reference/yaml-spec.rst#L949-L961)),
  through a jobs controller VM of 4+ vCPUs with a 50 GB disk
  ([`sky/jobs/constants.py` L57–61](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/sky/jobs/constants.py#L57-L61)).
  **Inferred:** not for the gateway: a relaunch moves away from its zonal data disk and its IP.
- **Inferred:** if the VM is deleted or its zone fails, recovery is running the same `sky launch`
  again in the same zone; the data disk reattaches by name (section 4) and the fixed IP is
  reattached (section 3). Both steps can be scripted.
- **Read:** `sky stop` keeps the disks, which are still billed
  ([`command.py` L3093–3095](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/sky/client/cli/command.py#L3093-L3095)).
- **Read:** unless a remote API server is set up, every SkyPilot command talks to an API server
  started on the local machine
  ([`reference/api-server/api-server.rst` L23–28](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/reference/api-server/api-server.rst#L23-L28)).
  The recommended remote API server is a Helm chart on Kubernetes
  ([`api-server-admin-deploy.rst` L6–16](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/reference/api-server/api-server-admin-deploy.rst#L6-L16)).
  **Inferred:** the record of the gateway cluster lives on the machine that launched it. A second
  person or agent can manage it with `sky` only through a shared API server, which is one more
  always-on service; otherwise they use `gcloud`.

## 3. Address, port, HTTPS and domain name

- **Read:** SkyPilot gives a GCP VM a `ONE_TO_ONE_NAT` access config with no `natIP`
  ([`sky/provision/gcp/config.py` L957–963](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/sky/provision/gcp/config.py#L957-L963)),
  which is an ephemeral external IP. None of SkyPilot's GCP settings names a static IP
  ([`reference/config.rst` L1193–1474](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/reference/config.rst#L1193-L1474)).
- **Read:** "an ephemeral IP address is released if you stop or delete the resource"
  ([Configure static external IP addresses](https://docs.cloud.google.com/compute/docs/ip-addresses/configure-static-external-ip-address)).
  A static address attached to a VM counts as in use whether the VM runs or is stopped
  ([Network pricing](https://cloud.google.com/vpc/network-pricing)).
- **Read:** an ephemeral address is promoted to static with
  `gcloud compute addresses create ADDRESS_NAME --addresses=IP_ADDRESS --region=REGION`, and stays
  attached to the VM
  ([Reserve a static external IP address](https://docs.cloud.google.com/vpc/docs/reserve-static-external-ip-address)).
  A static address is put on an existing VM by removing its current access config, then
  `gcloud compute instances add-access-config INSTANCE_NAME --access-config-name=NAME
  --address=IP_ADDRESS`
  ([Configure static external IP addresses](https://docs.cloud.google.com/compute/docs/ip-addresses/configure-static-external-ip-address)).
- **Read, prices:** static or ephemeral IP in use on a standard VM $0.005/h ($3.65/month); a static
  IP reserved but unattached $0.01/h ($7.30/month)
  ([Network pricing](https://cloud.google.com/vpc/network-pricing)).
- **Inferred:** after `sky down` and a fresh `sky launch`, the new VM has a new ephemeral IP; a script
  swaps the static one back on with the two commands above.
- **Read:** every port in `resources.ports` "will be exposed to the public Internet" through a
  firewall rule
  ([`yaml-spec.rst` L647–662](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/reference/yaml-spec.rst#L647-L662));
  on GCP the rule allows source `0.0.0.0/0` to the cluster's network tag
  ([`sky/provision/gcp/instance_utils.py` L636–651](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/sky/provision/gcp/instance_utils.py#L636-L651)).
  SkyPilot also requires SSH (port 22) open from anywhere
  ([`sky/provision/gcp/constants.py` L274–302](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/sky/provision/gcp/constants.py#L274-L302)).
- **Read:** SkyServe's HTTPS takes a certificate and key file that you supply
  ([`serving/https.rst` L15–31](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/serving/https.rst#L15-L31)).
  Plain `sky launch` has no HTTPS feature.
- **Read:** "By default, Caddy serves all sites over HTTPS", obtaining public certificates itself
  when the domain's A/AAAA records point to the server, ports 80 and 443 are open externally, and its
  data directory is writeable and persistent
  ([Caddy: Automatic HTTPS](https://caddyserver.com/docs/automatic-https)).
  **Inferred:** open ports 80 and 443 in the YAML, run Caddy in front of the gateway, and keep
  Caddy's data directory on the persistent data disk so certificates survive a rebuild.
- **Read:** GCP's managed alternative, a load balancer with a Google-managed certificate, costs
  $0.025/h ($18.25/month) for the forwarding rule plus $0.008 per GiB processed
  ([Network pricing](https://cloud.google.com/vpc/network-pricing)). **Inferred:** more than the VM.
- **Not established:** the DNS side. SkyPilot does nothing for DNS. **Inferred:** one A record,
  added by whoever controls the lab's domain.

## 4. Data that must outlive the VM

### SkyPilot volumes and persistent disks

- **Read:** "Volumes are currently supported on Kubernetes clusters and RunPod"
  ([`reference/volumes.rst` L14](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/reference/volumes.rst#L14));
  the volume types are `k8s-pvc`, `k8s-hostpath` and `runpod-network-volume`
  ([`sky/utils/volume.py` L35–39](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/sky/utils/volume.py#L35-L39)).
- **Read:** the boot disk is created with `autoDelete: true`
  ([`gcp-ray.yml.j2` L152–157](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/sky/templates/gcp-ray.yml.j2#L152-L157)).
  **Inferred:** anything on the boot disk is lost at `sky down`.
- **Read:** a separate field, `resources.volumes`, is in SkyPilot's YAML schema
  ([`sky/utils/schemas.py` L433–466](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/sky/utils/schemas.py#L433-L466))
  but in none of its docs. On GCP, a network volume must have a `name`; if a disk of that name exists
  in the region it is attached, after a zone check
  ([`sky/clouds/gcp.py` L1312–1337](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/sky/clouds/gcp.py#L1312-L1337)),
  otherwise it is created
  ([L1358–1379](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/sky/clouds/gcp.py#L1358-L1379)).
  `auto_delete` defaults to false, and a disk with no `disk_tier` gets the "best" tier
  ([`sky/resources.py` L1138–1139, L1179–1183](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/sky/resources.py#L1138-L1183)).
  At boot SkyPilot formats the disk as ext4 only if it is not ext4 already, mounts it and adds it to
  `/etc/fstab` with `nofail`
  ([`sky/provision/gcp/constants.py` L132–185](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/sky/provision/gcp/constants.py#L132-L185)).
- **Inferred:** create the data disk with `gcloud` first, at a chosen type and size, then name it in
  `resources.volumes`. It survives `sky down` and reattaches on the next launch in the same zone.
  Creating it through SkyPilot instead risks the "best" tier, the costliest disk type the machine
  allows.
- **Not established:** whether `resources.volumes` is a supported interface. Nothing in the docs
  promises it.

### Cloud SQL

- **Read, prices:** `db-f1-micro` (shared vCPU, 0.6 GB) $0.0105/h ($7.67/month); `db-g1-small`
  (shared vCPU, 1.7 GB) $0.035/h ($25.55/month); high availability doubles each; SSD storage
  $0.000232877 per GiB-hour (about $0.17 per GiB-month); backups $0.000109589 per GiB-hour (about
  $0.08 per GiB-month). "Shared CPU machine types (db-f1-micro and db-g1-small) are not covered by
  the Cloud SQL SLA" ([Cloud SQL pricing](https://cloud.google.com/sql/pricing)).
- **Read:** automated backup retention is set from 1 to 365; a final backup can be kept at deletion;
  automated backups "are deleted on a rolling basis, one backup per day, after the instance is
  deleted"; on-demand backups are kept until deleted
  ([About Cloud SQL backups](https://docs.cloud.google.com/sql/docs/postgres/backup-recovery/backups)).
- **Not established:** whether a new instance has automated backups and point-in-time recovery on by
  default; neither page read says.
- **Read:** a VM reaches Cloud SQL by private IP once the VPC network has a private services access
  connection, or by public IP through the Cloud SQL Auth Proxy
  ([Connect from Compute Engine](https://docs.cloud.google.com/sql/docs/postgres/connect-instance-compute-engine)).
- **Read:** SkyPilot's docs use Cloud SQL only as the database of SkyPilot's own API server on GKE
  ([`reference/api-server/examples/example-deploy-gcp-cloud-sql.rst`](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/reference/api-server/examples/example-deploy-gcp-cloud-sql.rst)).
  **Inferred:** Cloud SQL is created and managed with `gcloud`, outside SkyPilot.

### Backups for PostgreSQL on a persistent disk

- **Read:** a snapshot schedule is made with `gcloud compute resource-policies create
  snapshot-schedule` (hourly, daily or weekly, with a required retention in days) and attached with
  `gcloud compute disks add-resource-policies`; by default its snapshots are kept if the disk is
  deleted
  ([Scheduled snapshots](https://docs.cloud.google.com/compute/docs/disks/scheduled-snapshots)).
- **Read, price:** standard snapshot storage $0.000068493 per GiB-hour, about $0.05 per GiB-month
  ([Disk and image pricing](https://cloud.google.com/compute/disks-image-pricing)).
- **Not established:** whether a snapshot of a disk under a running PostgreSQL restores cleanly; GCP's
  page does not say. **Inferred:** add a nightly `pg_dump` to Cloud Storage as the restore that does
  not depend on it.

### Side by side

| | PostgreSQL on a persistent disk | Cloud SQL |
| --- | --- | --- |
| Monthly cost, 20 GB | about $2 disk + snapshots | from $7.67 + $3.40 storage + backups |
| Survives `sky down` | yes, named disk with `auto_delete: false` (inferred) | yes, separate service |
| Backups | snapshot schedule, plus a dump (inferred) | built in; defaults not established |
| PostgreSQL upgrades | by hand, inside `setup` | managed by GCP (not read in detail) |
| Managed by SkyPilot | attached through an undocumented field | no |
| Works with SkyServe | no, single writer (inferred) | yes |

## 5. Secrets

- **Read:** SkyPilot's `secrets` field is "similar to envs" but limited to `setup` and `run`, and its
  values are "redacted in the entrypoint/YAML in the dashboard"; `--secret` sets one from the command
  line
  ([`yaml-spec.rst` L1060–1069](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/reference/yaml-spec.rst#L1060-L1069)).
  The backend merges envs and secrets into plain environment variables for the job
  ([`sky/backends/cloud_vm_ray_backend.py` L7030–7034](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/sky/backends/cloud_vm_ray_backend.py#L7030-L7034)).
- **Read:** SkyPilot has no secret store of its own, and a search of its docs and source for
  "secretmanager" or "Secret Manager" finds nothing.
- **Read:** by default (`LOCAL_CREDENTIALS`) SkyPilot uploads the launching user's local cloud
  credentials to the VM; `remote_identity` can instead name a service account
  ([`reference/config.rst` L1089–1112, L1431–1440](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/reference/config.rst#L1089-L1440)).
- **Read:** reading a secret needs the Secret Manager Secret Accessor role
  (`roles/secretmanager.secretAccessor`), and a Compute Engine VM needs the `cloud-platform` scope;
  `gcloud secrets versions access` reads a version
  ([Access a secret version](https://docs.cloud.google.com/secret-manager/docs/access-secret-version)).
- **Read, price:** 6 active secret versions and 10,000 access operations a month are free; beyond
  that $0.000082192 per version-hour (about $0.06 a month) and $0.03 per 10,000 accesses
  ([Secret Manager pricing](https://cloud.google.com/secret-manager/pricing);
  [Free cloud features](https://docs.cloud.google.com/free/docs/free-cloud-features)).
- **Inferred:** keep provider keys in Secret Manager. Give the VM a dedicated service account that
  can only read those secrets (and connect to Cloud SQL, if used), name it in `gcp.remote_identity`,
  and have the gateway's start script fetch the keys. A person's own GCP credentials then never sit
  on an internet-facing VM.

## 6. Upgrades, logs and alerts

- **Read:** `sky exec` reruns only `run` and skips provisioning, `setup` and file syncing; `sky
  launch` on an existing cluster reapplies them
  ([`command.py` L1601–1617](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/sky/client/cli/command.py#L1601-L1617)).
  **Inferred:** an upgrade, a new gateway pin included, is: snapshot the data disk, change the pin in
  `setup`, run `sky launch -c gateway gateway.yaml` again, and let systemd restart the service. The
  data disk is untouched. The service is down while it restarts; SkyPilot's own note says a VM
  deployment has no graceful upgrade.
- **Inferred:** a larger change (machine type, OS image) is `sky down` and `sky launch`; the data disk
  reattaches and the static IP is swapped back on.
- **Read:** the Ops Agent is "the primary agent for collecting telemetry from your Compute Engine
  instances"; it collects `/var/log/syslog` with no configuration and supports journald as a source
  ([Ops Agent overview](https://docs.cloud.google.com/logging/docs/agent/ops-agent)). Cloud Logging
  charges $0.50/GiB after the first 50 GiB per project per month
  ([Observability pricing](https://cloud.google.com/products/observability/pricing)).
- **Read:** an uptime check sends HTTP, HTTPS or TCP requests from several places in the world; an
  alerting policy on it notifies a channel when the target fails; `gcloud monitoring uptime create`
  makes one ([Uptime checks](https://docs.cloud.google.com/monitoring/uptime-checks)). Pricing: the
  first 1 million executions per project are free, then $0.30 per 1,000; alerting policies list
  $0.35 a month per metric referenced, with an effective date of 2027-09-01
  ([Observability pricing](https://cloud.google.com/products/observability/pricing)).
- **Read:** SkyPilot collects usage statistics by default; `SKYPILOT_DISABLE_USAGE_COLLECTION=1`
  turns it off
  ([`reference/logging.rst` L1–23](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/reference/logging.rst#L1-L23)).

## 7. Which GCP regions can reach every provider

- **Read, OpenAI:** using the API outside its listed countries and territories "may result in your
  account being blocked or suspended"
  ([Supported countries and territories](https://developers.openai.com/api/docs/supported-countries),
  redirected from `platform.openai.com/docs/supported-countries`).
- **Read, Anthropic:** one list covers the apps and the API; "Any country or region not listed is
  unsupported" ([Supported countries and regions](https://www.anthropic.com/supported-countries)).
- **Read, DeepSeek:** no country list. Its Open Platform terms (effective 2026-04-29) give no
  warranty "that the Services are available or will continue to be available in certain
  jurisdictions" and bar use in countries under comprehensive sanctions
  ([DeepSeek Open Platform Terms of Service](https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html)).
- **Read, TypeSafe:** no country list in its docs. Its Master Customer Agreement (last updated
  2026-09-23) bars only use from U.S.-embargoed countries and use against U.S. export rules
  (section 16.12) ([Master Customer Agreement](https://typesafe.ai/legal/mca);
  [TypeSafe legal docs](https://docs.typesafe.ai/legal.md)).

GCP regions by country
([Regions and zones](https://docs.cloud.google.com/compute/docs/regions-zones)), checked against the
two lists:

| GCP regions | Country | OpenAI | Anthropic |
| --- | --- | --- | --- |
| `us-central1`, `us-east1`, `us-east4`, `us-east5`, `us-south1`, `us-west1` to `us-west4` | United States | yes | yes |
| `northamerica-northeast1`, `northamerica-northeast2` | Canada | yes | yes |
| `northamerica-south1` | Mexico | yes | yes |
| `southamerica-east1` / `southamerica-west1` | Brazil / Chile | yes | yes |
| `europe-west1`, `europe-west2`, `europe-west4`, `europe-west6`, `europe-west9` | Belgium, UK, Netherlands, Switzerland, France | yes | yes |
| `europe-west3`, `europe-west10` / `europe-west8`, `europe-west12` | Germany / Italy | yes | yes |
| `europe-central2`, `europe-north1`, `europe-north2`, `europe-southwest1` | Poland, Finland, Sweden, Spain | yes | yes |
| `me-central1`, `me-central2`, `me-west1` | Qatar, Saudi Arabia, Israel | yes | yes |
| `africa-south1` | South Africa | yes | yes |
| `asia-east1` | Taiwan | yes | yes |
| `asia-east2` | Hong Kong | **no** | **no** |
| `asia-northeast1`, `asia-northeast2` / `asia-northeast3` | Japan / South Korea | yes | yes |
| `asia-south1`, `asia-south2` | India | yes | yes |
| `asia-southeast1`, `asia-southeast2`, `asia-southeast3` | Singapore, Indonesia, Thailand | yes | yes |
| `australia-southeast1`, `australia-southeast2` | Australia | yes | yes |

**Inferred:** what matters is where the gateway VM sits, since it alone calls the providers; lab
clients anywhere reach the gateway. A US region (`us-central1`, `us-east1` or `us-west1`) is on
every list, is the one priced above and holds the free `e2-micro`. Closer to Asia, `asia-east1`,
`asia-northeast1` and `asia-southeast1` are on both lists; prices there were not read.

## A sketch of the setup

Shortest sequence. **Person** marks a step that needs a person at a console or a browser login;
**script** marks one an agent skill can run end to end. Under the lab's working style, commands on
the live gateway are proposed by the skill and run by the user.

1. **Person.** Create or pick the GCP project and link a billing account.
2. **Person, once per machine.** Install `gcloud` and SkyPilot, then `gcloud init` and
   `gcloud auth application-default login`, both of which open a browser
   ([`getting-started/installation.rst` L515–520](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/getting-started/installation.rst#L515-L520)).
   **Script:** `sky check gcp`.
3. **Script.** Enable the Compute Engine, Secret Manager, Logging and Monitoring APIs (inferred:
   `gcloud services enable`, not read).
4. **Person.** Copy each provider key from that provider's console into Secret Manager
   (`gcloud secrets create`, value from standard input). Only a person can get the key.
5. **Script.** Create the gateway's service account, grant it Secret Accessor on those secrets, and
   set `gcp.remote_identity` to it in `~/.sky/config.yaml`.
6. **Script.** Create the data disk in one zone with `gcloud compute disks create`, and attach a
   daily snapshot schedule.
7. **Script.** `sky launch -c gateway gateway.yaml`. The YAML pins the zone in `infra`
   (`gcp/<region>/<zone>`,
   [`yaml-spec.rst` L212–223](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/reference/yaml-spec.rst#L212-L223)), names
   `instance_type: e2-small` and `disk_size: 30`, opens `ports: [80, 443]`, lists the data disk under
   `resources.volumes` with `auto_delete: false`, sets no autostop, and its `setup` installs
   PostgreSQL (data on the disk), the gateway at its pinned version, Caddy, and systemd units for
   all three.
8. **Script.** Promote the VM's IP to static with `gcloud compute addresses create`.
9. **Person.** Add the DNS A record for the gateway's name at the lab's DNS provider. Caddy then
   gets its certificate by itself.
10. **Script.** Create an uptime check on the HTTPS health URL and an alerting policy.
    **Person:** choose who is notified (inferred).
11. **Upgrade, script.** Snapshot the data disk, change the pin, run `sky launch` again (step 7).
12. **Teardown, script.** `sky down gateway`. The data disk, snapshots and static IP remain and are
    still billed until deleted with `gcloud`.

**Read:** SkyPilot ships an official agent skill for Claude Code and other agents
([`getting-started/skill.rst` L6–22](https://github.com/skypilot-org/skypilot/blob/39b151e11075f1d0029f8fc411a42334775fb3e7/docs/source/getting-started/skill.rst#L6-L22)).
**Inferred:** the lab's skill can lean on it for the `sky` steps and add the `gcloud` ones.

## Where SkyPilot does not fit

- **A fixed IP.** No setting for it; ephemeral IPs are released on stop (section 3).
- **HTTPS, certificates and DNS.** Nothing for plain launches; SkyServe takes files you supply.
- **Secrets.** No store; values reach the VM as plain environment variables; nothing reads Secret
  Manager.
- **The VM's identity.** By default it carries the launching person's GCP credentials; a scoped
  service account has to be made with `gcloud`.
- **Persistent disks on GCP.** Only through an undocumented field; documented volumes are Kubernetes
  and RunPod only.
- **Cloud SQL, snapshots, logging agents, uptime checks and alerts.** All `gcloud`, outside SkyPilot.
- **Restarting the service after a reboot.** Not established for SkyPilot's job; systemd covers it.
- **Firewall scope.** Ports and SSH open to every address; narrowing them is a `gcloud` firewall
  edit (inferred).
- **Shared control.** The cluster's record lives with one machine's local API server unless the lab
  runs a shared one.
- **SkyServe for this service.** A second, larger VM, beta, and its new-replica-first updates clash
  with a database disk on the replica.

**Inferred:** SkyPilot's part reduces to choosing and creating the VM, opening ports, attaching the
disk, running `setup`, and reapplying it all on upgrade. `gcloud compute instances create` with a
startup script could do the same; SkyPilot's value here is one re-runnable YAML and an agent skill
that already knows it.

## Not established

- What SkyPilot does with the `run` job after GCP restarts the VM.
- Whether `resources.volumes` is a supported interface, and which disk type its "best" tier gives an
  `e2` VM.
- Which machine type SkyPilot picks for the SkyServe controller on GCP.
- Whether a new Cloud SQL instance has automated backups and point-in-time recovery on by default.
- Whether a persistent disk snapshot taken under a running PostgreSQL restores cleanly.
- Prices outside `us-central1`; Cloud DNS and domain prices.
- Whether DeepSeek or TypeSafe block any GCP region in practice.
- Whether billing linking, API enabling and alert notification channels can all be done with
  `gcloud` alone; not read.
- The gateway's own memory and disk needs, which depend on the gateway software.
