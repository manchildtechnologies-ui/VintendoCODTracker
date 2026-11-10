Option Explicit

Dim sh, fso, root, dataDir, localApp, codmunity, processList, proc, processEnv
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
dataDir = root & "\browser-e2e-work\chrome-test-data"
localApp = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%")
codmunity = localApp & "\CODMunity Desktop\codmunity-desktop.exe"

Set processList = GetObject("winmgmts:\\.\root\cimv2").ExecQuery("SELECT Name FROM Win32_Process")
Dim hasCodmunity, hasChrome
hasCodmunity = False
hasChrome = False
For Each proc In processList
    If LCase(proc.Name) = "codmunity-desktop.exe" Then hasCodmunity = True
    If LCase(proc.Name) = "chrome.exe" Then hasChrome = True
Next

Set processList = Nothing
Set processEnv = sh.Environment("PROCESS")
processEnv("VINTENDO_DATA_DIR") = dataDir
processEnv("VINTENDO_TRACK_BOTH") = "1"
processEnv("VINTENDO_AUTO_START_TRACKING") = "1"
sh.CurrentDirectory = root

If Not hasCodmunity And fso.FileExists(codmunity) Then sh.Run Chr(34) & codmunity & Chr(34), 0, False
If Not hasChrome Then
    Dim chromePaths, chromePath, i
    chromePaths = Array(sh.ExpandEnvironmentStrings("%ProgramFiles%\Google\Chrome\Application\chrome.exe"), sh.ExpandEnvironmentStrings("%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"), localApp & "\Google\Chrome\Application\chrome.exe")
    For i = 0 To UBound(chromePaths)
        If fso.FileExists(chromePaths(i)) Then
            chromePath = chromePaths(i)
            Exit For
        End If
    Next
    If Len(chromePath) > 0 Then sh.Run Chr(34) & chromePath & Chr(34) & " https://www.meta.ai/", 0, False
End If

sh.Run Chr(34) & root & "\.venv\Scripts\pythonw.exe" & Chr(34) & " " & Chr(34) & root & "\launcher.py" & Chr(34), 0, False
