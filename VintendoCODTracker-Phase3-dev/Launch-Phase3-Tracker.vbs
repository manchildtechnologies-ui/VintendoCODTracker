Option Explicit

Dim sh, fso, root, dataDir, localApp, codmunity, processList, proc, processEnv
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
dataDir = root & "\browser-e2e-work\chrome-test-data"
localApp = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%")
codmunity = localApp & "\CODMunity Desktop\codmunity-desktop.exe"

Set processList = GetObject("winmgmts:\\.\root\cimv2").ExecQuery("SELECT Name FROM Win32_Process")
Dim hasCodmunity
hasCodmunity = False
For Each proc In processList
    If LCase(proc.Name) = "codmunity-desktop.exe" Then hasCodmunity = True
Next

Set processList = Nothing
Set processEnv = sh.Environment("PROCESS")
processEnv("VINTENDO_DATA_DIR") = dataDir
processEnv("VINTENDO_TRACK_BOTH") = "1"
processEnv("VINTENDO_AUTO_START_TRACKING") = "1"
sh.CurrentDirectory = root

If Not hasCodmunity And fso.FileExists(codmunity) Then sh.Run Chr(34) & codmunity & Chr(34) & " --start-minimized", 0, False

sh.Run Chr(34) & root & "\.venv\Scripts\pythonw.exe" & Chr(34) & " " & Chr(34) & root & "\launcher.py" & Chr(34), 0, False
