' Codex Sandbox Guard Launcher — 开机自启，隐藏运行
CreateObject("Wscript.Shell").Run "powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -File ""%USERPROFILE%\.codex\sandbox-guard.ps1""", 0, False
