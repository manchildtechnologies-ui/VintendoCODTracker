Option Explicit

Dim sh, fso, root, dataDir, localApp, codmunity, processList, proc, hasCodmunity, hasTracker, activated, processEnv, trackerExe, pendingExe, activeExe, pathPart, cleanPath, currentPath, installError, candidate, newestDate, buildStamp, buildSequence
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
trackerExe = root & "\VintendoCODTracker.exe"
pendingExe = root & "\VintendoCODTracker.pending.exe"
dataDir = root & "\browser-e2e-work\chrome-test-data" ' legacy path (unused; VINTENDO_DATA_DIR override removed so data lives in %LOCALAPPDATA%)
localApp = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%")
codmunity = localApp & "\CODMunity Desktop\codmunity-desktop.exe"

Set processList = GetObject("winmgmts:\\.\root\cimv2").ExecQuery("SELECT Name FROM Win32_Process")
hasCodmunity = False
hasTracker = False
For Each proc In processList
    If LCase(proc.Name) = "codmunity-desktop.exe" Then hasCodmunity = True
    If LCase(proc.Name) = "vintendocodtracker.exe" Or LCase(proc.Name) = "vintendocodtracker.launch.exe" Or InStr(1, LCase(proc.Name), "vintendocodtracker.launch-", vbTextCompare) = 1 Then hasTracker = True
Next

Set processList = Nothing

' Single-instance guard: the exe cold-starts slowly with a hidden window, so
' extra clicks used to pile up several copies before the in-app guard was
' listening. A repeat click now focuses the running Tracker instead.
If hasTracker Then
    On Error Resume Next
    activated = sh.AppActivate("Vintendo COD Tracker")
    On Error GoTo 0
    If fso.FileExists(pendingExe) Then
        MsgBox "The new tracker build is ready. Close Vintendo COD Tracker, then click this shortcut again to start the updated build.", vbInformation, "Vintendo COD Tracker"
    ElseIf Not activated Then
        MsgBox "Vintendo COD Tracker is already running." & vbCrLf & "Check the taskbar or system tray.", vbInformation, "Vintendo COD Tracker"
    End If
    WScript.Quit 0
End If

' Copy a staged build to a separate launch path. Never replace the installed
' executable itself, which may be protected or still held open by Windows.
If fso.FileExists(pendingExe) Then
    buildStamp = Year(Now) & Right("0" & Month(Now), 2) & Right("0" & Day(Now), 2) & "-" & Right("0" & Hour(Now), 2) & Right("0" & Minute(Now), 2) & Right("0" & Second(Now), 2)
    activeExe = root & "\VintendoCODTracker.launch-" & buildStamp & ".exe"
    buildSequence = 1
    Do While fso.FileExists(activeExe)
        activeExe = root & "\VintendoCODTracker.launch-" & buildStamp & "-" & buildSequence & ".exe"
        buildSequence = buildSequence + 1
    Loop
    On Error Resume Next
    fso.CopyFile pendingExe, activeExe, False
    If Err.Number <> 0 Then
        installError = Err.Description
        Err.Clear
        On Error GoTo 0
        MsgBox "The new tracker build could not be prepared: " & installError, vbExclamation, "Vintendo COD Tracker"
        WScript.Quit 1
    End If
    fso.DeleteFile pendingExe, True
    On Error GoTo 0
    trackerExe = activeExe
Else
    newestDate = 0
    For Each candidate In fso.GetFolder(root).Files
        If InStr(1, LCase(candidate.Name), "vintendocodtracker.launch-", vbTextCompare) = 1 And LCase(fso.GetExtensionName(candidate.Name)) = "exe" Then
            If candidate.DateLastModified > newestDate Then
                newestDate = candidate.DateLastModified
                trackerExe = candidate.Path
            End If
        End If
    Next
    If trackerExe = root & "\VintendoCODTracker.exe" And fso.FileExists(root & "\VintendoCODTracker.launch.exe") Then trackerExe = root & "\VintendoCODTracker.launch.exe"
End If

Set processEnv = sh.Environment("PROCESS")
' Single data home: %LOCALAPPDATA%\Vintendo COD Tracker (no VINTENDO_DATA_DIR override).
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

' Single-app build: never auto-start the external desktop app.

sh.Run Chr(34) & trackerExe & Chr(34), 1, False
