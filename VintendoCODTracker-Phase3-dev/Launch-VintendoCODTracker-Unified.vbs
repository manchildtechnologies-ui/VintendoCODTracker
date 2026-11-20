Option Explicit

Dim sh, fso, root, processEnv, activated
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)

' Focus the running Tracker instead of stacking a second copy.
' No WMI process enumeration: a WMI class overload (0x80041021)
' previously aborted this script before anything launched.
On Error Resume Next
activated = sh.AppActivate("Vintendo COD Tracker")
On Error GoTo 0
If activated Then WScript.Quit 0

Set processEnv = sh.Environment("PROCESS")
processEnv("VINTENDO_TRACK_BOTH") = "1"
processEnv("VINTENDO_AUTO_START_TRACKING") = "1"
sh.CurrentDirectory = root

sh.Run Chr(34) & root & "\.venv\Scripts\pythonw.exe" & Chr(34) & " " & Chr(34) & root & "\launcher.py" & Chr(34), 1, False
