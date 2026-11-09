# Reach the lab gateway

The lab gateway runs on a cloud server that you reach only through SSH. Your code calls it
through an SSH tunnel: a connection that carries a port on your machine to the gateway. In
the commands below, replace `<gateway-ip>` with the address the gateway's admin gives you.

## Get your key added

1. If you have no SSH key, make one with `ssh-keygen -t ed25519`.
2. Send the admin your public key: the one line in the key file whose name ends in `.pub`.
   Never send the file without `.pub`, which is your private key.
3. Wait for the admin to say your key is added. You get no account of your own and no
   password.

Your key opens the tunnel and nothing else. If you try to log in with it, as in
`ssh tunnel@<gateway-ip>`, you see `This account is currently not available.` and the
connection closes. That is expected.

## Open the tunnel

Run this in its own terminal, and leave it running while you work:

```bash
ssh -N -L 8000:127.0.0.1:8000 tunnel@<gateway-ip>
```

It prints nothing while the tunnel is open. Port 8000 on your machine now leads to the
gateway. To stop the tunnel, press Ctrl-C.

If port 8000 is in use on your machine, choose another local port, such as 8001. Change only
the first number: `-L 8001:127.0.0.1:8000`.

## Set `LABLLM_GATEWAY`

The library finds the gateway from one environment variable, the tunnel's local address:

```bash
export LABLLM_GATEWAY=http://127.0.0.1:8000
```

Use the local port you chose. To set it in every new terminal, add the line to your shell's
startup file, such as `~/.zshrc` or `~/.bashrc`.

## Check that it works

Save this as `check_gateway.py`:

```python
--8<--
tests/tutorial/check_gateway.py
--8<--
```

Run it with the tunnel open:

```bash
pixi run python check_gateway.py
```

It prints the model's reply, `OK` or close to it. The first person to send this exact
request pays a small fraction of a cent. The gateway answers everyone after that from its
cache, free.

## If it does not work

- **`set LABLLM_GATEWAY to the local address of your tunnel`**: the variable is not set in
  this terminal. Set it as above.
- **`the gateway does not answer`**, after about a minute: the tunnel is not open, or it
  closed. Open it again.
- **`Permission denied (publickey)`** from `ssh`: the admin has not added your key yet, or
  `ssh` offered a different key. Name your key with `-i`, followed by the path of your
  private key file.
- **A cluster compute node** with no internet cannot reach the gateway. Run your pass on a
  machine that has internet.

If the gateway restarts during a call, the call waits and then goes through, because the
library sends it again for up to 60 seconds.
