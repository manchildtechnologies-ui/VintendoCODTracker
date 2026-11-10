Option Explicit

Dim sh, fso, root, dataDir, previewRoot, sourceData, stamp, processEnv, pythonw
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
previewRoot = root & "\codmunity-tab-preview-data"
sourceData = root & "\browser-e2e-work\chrome-test-data"
stamp = Year(Now) & Right("0" & Month(Now), 2) & Right("0" & Day(Now), 2) & "-" & Right("0" & Hour(Now), 2) & Right("0" & Minute(Now), 2) & Right("0" & Second(Now), 2) & "-" & CStr(Int(Timer * 1000))
dataDir = previewRoot & "\run-" & stamp
pythonw = root & "\.venv\Scripts\pythonw.exe"

If Not fso.FileExists(pythonw) Then
    MsgBox "The development Python environment was not found:" & vbCrLf & pythonw, vbExclamation, "CODMunity Tab Preview"
    WScript.Quit 1
End If

If Not fso.FolderExists(previewRoot) Then fso.CreateFolder(previewRoot)
If Not fso.FolderExists(dataDir) Then fso.CreateFolder(dataDir)
Set processEnv = sh.Environment("PROCESS")
processEnv("VINTENDO_DATA_DIR") = dataDir
processEnv("VINTENDO_TAB_PROTOTYPE") = "1"
processEnv("VINTENDO_TAB_PREVIEW_SOURCE") = sourceData
processEnv("VINTENDO_TRACK_BOTH") = ""
processEnv("VINTENDO_AUTO_START_TRACKING") = ""
sh.CurrentDirectory = root
sh.Run Chr(34) & pythonw & Chr(34) & " " & Chr(34) & root & "\launcher.py" & Chr(34), 0, False
