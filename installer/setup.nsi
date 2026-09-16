Unicode true
!include "FileFunc.nsh"
!include "x64.nsh"
Name "GlyphLink Setup"
OutFile "../release/GlyphLinkSetup-1.3.6.exe"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 32
CRCCheck force
AutoCloseWindow true
ShowInstDetails nevershow
BrandingText "GlyphLink"
VIProductVersion "1.3.6.0"
VIAddVersionKey /LANG=1033 "ProductName" "GlyphLink Setup"
VIAddVersionKey /LANG=1033 "FileDescription" "GlyphLink Windows and Android installer"
VIAddVersionKey /LANG=1033 "FileVersion" "1.3.6"
VIAddVersionKey /LANG=1033 "LegalCopyright" "GlyphLink / Adam Ali"
Page instfiles
Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "GlyphLink requires 64-bit Windows."
    Abort
  ${EndIf}
FunctionEnd
Section "Preparing GlyphLink"
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  DetailPrint "Preparing the included apps..."
  File "glyphlink_setup.py"
  File "installer_core.py"
  File "setup_controller.py"
  File /r "payload"
  ${GetParameters} $0
  HideWindow
  ClearErrors
  ExecWait '"$PLUGINSDIR\payload\app\runtime\pythonw.exe" -B -E -s "$PLUGINSDIR\glyphlink_setup.py" $0' $1
  IfErrors failed
  SetErrorLevel $1
  Quit
failed:
  MessageBox MB_OK|MB_ICONSTOP "Windows could not open GlyphLink Setup. The included runtime could not start."
  SetErrorLevel 1
SectionEnd
