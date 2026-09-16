Unicode true
!include "FileFunc.nsh"
Name "GlyphLink"
OutFile "payload/app/GlyphLink.exe"
RequestExecutionLevel user
SilentInstall silent
AutoCloseWindow true
VIProductVersion "1.3.6.0"
VIAddVersionKey /LANG=1033 "ProductName" "GlyphLink"
VIAddVersionKey /LANG=1033 "FileDescription" "GlyphLink desktop application"
VIAddVersionKey /LANG=1033 "FileVersion" "1.3.6"
VIAddVersionKey /LANG=1033 "LegalCopyright" "GlyphLink / Adam Ali"
Section
  SetOutPath "$EXEDIR"
  ${GetParameters} $0
  ClearErrors
  ExecWait '"$EXEDIR\runtime\pythonw.exe" -B -E -s "$EXEDIR\glyphlink.py" $0' $1
  IfErrors failed
  SetErrorLevel $1
  Quit
failed:
  MessageBox MB_OK|MB_ICONSTOP "GlyphLink could not start its included runtime. Please run the complete GlyphLink installer again."
  SetErrorLevel 1
SectionEnd
