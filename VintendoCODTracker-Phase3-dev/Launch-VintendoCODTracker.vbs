Option Explicit

Dim sh, fso, root, dataDir, localApp, codmunity, processList, proc, hasCodmunity, hasTracker, activated, processEnv, trackerExe, pendingExe, installError, pathPart, cleanPath, currentPath
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
trackerExe = root & "\VintendoCODTracker.exe"
pendingExe = root & "\VintendoCODTracker.pending.exe"
dataDir = root & "\browser-e2e-work\chrome-test-data"
localApp = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%")
codmunity = localApp & "\CODMunity Desktop\codmunity-desktop.exe"

Set processList = GetObject("winmgmts:\\.\root\cimv2").ExecQuery("SELECT Name FROM Win32_Process")
hasCodmunity = False
hasTracker = False
For Each proc In processList
    If LCase(proc.Name) = "codmunity-desktop.exe" Then hasCodmunity = True
    If LCase(proc.Name) = "vintendocodtracker.exe" Then hasTracker = True
Next

Set processList = Nothing

' Single-instance guard: the exe cold-starts slowly with a hidden window, so
' extra clicks used to pile up several copies before the in-app guard was
' listening. A repeat click now focuses the running Tracker instead.
If hasTracker Then
    On Error Resume Next
    activated = sh.AppActivate("Vintendo COD Tracker")
    On Error GoTo 0
    If Not activated Then MsgBox "Vintendo COD Tracker is already running." & vbCrLf & "Check the taskbar or system tray.", vbInformation, "Vintendo COD Tracker"
    WScript.Quit 0
End If

' Install a staged build only after all running tracker processes have exited.
If fso.FileExists(pendingExe) Then
    On Error Resume Next
    fso.CopyFile pendingExe, trackerExe, True
    If Err.Number <> 0 Then
        installError = Err.Description
        Err.Clear
        On Error GoTo 0
        MsgBox "The new tracker build could not be installed: " & installError, vbExclamation, "Vintendo COD Tracker"
        WScript.Quit 1
    End If
    fso.DeleteFile pendingExe, True
    On Error GoTo 0
End If

Set processEnv = sh.Environment("PROCESS")
processEnv("VINTENDO_DATA_DIR") = dataDir
processEnv("VINTENDO_TRACK_BOTH") = "1"
processEnv("VINTENDO_AUTO_START_TRACKING") = "1"
currentPath = sh.ExpandEnvironmentStrings("%PATH%")
cleanPath = ""
For Each pathPart In Split(currentPath, ";")
    If Len(Trim(pathPart)) > 0 And InStr(1, pathPart, "codex-runtimes", vbTextCompare) = 0 And InStr(1, pathPart, "OpenAI.CodexPrimaryRuntime", vbTextCompare) = 0 Then
        If Len(cleanPath) > 0 Then cleanPath = cleanPath & ";"
        cleanPath = cleanPath & pathPart
    End If
Next
processEnv("PATH") = cleanPath
sh.CurrentDirectory = root

If Not hasCodmunity And fso.FileExists(codmunity) Then sh.Run Chr(34) & codmunity & Chr(34) & " --start-minimized", 0, False

sh.Run Chr(34) & trackerExe & Chr(34), 1, False
