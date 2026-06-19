# VS Code Tunnel Mobile Guide

This guide is for editing the bot remotely through VS Code Tunnel.

## Setup

1. Install VS Code on the host.
2. Open the cloned `punch_relay` repository folder.
3. Sign in to VS Code with GitHub or Microsoft.
4. Run `Remote Tunnels: Turn on Remote Tunnel Access`.
5. From another device, open `https://vscode.dev`, connect to the tunnel, and open the same repository folder.

The repository can live in any folder. The service scripts use their own script directory as the project root.

## Editing Flow

1. Edit `bot_all_in_one.py` or related project files.
2. Run:

```powershell
python -m py_compile bot_all_in_one.py
```

3. If slash commands changed, restart with resync:

```powershell
powershell -ExecutionPolicy Bypass -File .\restart_bot_resync_admin.ps1
```

4. For normal restart:

```powershell
powershell -ExecutionPolicy Bypass -File .\restart_bot_admin.ps1
```

Both restart scripts require Administrator privileges.

## Safety

- Do not commit `.env`, logs, JSON runtime state, or backup files.
- Avoid restarting close to scheduled punch times unless the change is urgent.
- Check `bot.log` after restarting when possible.
