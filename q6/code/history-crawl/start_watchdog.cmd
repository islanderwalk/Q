@echo off
rem Copied into the Windows Startup folder so a reboot brings everything back: the watchdog starts the crawlobs stack in WSL,
rem the three crawlers, and backfills what the stack missed. Delete the copy in shell:startup to turn that off.
start "" "%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe" "%USERPROFILE%\q6dl\runtime\history-crawl\watchdog.py"
