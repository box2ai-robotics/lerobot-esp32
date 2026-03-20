@echo off
echo Creating com0com pair COM60 - COM61...
"C:\Program Files (x86)\com0com\setupc.exe" install PortName=COM60 PortName=COM61
echo.
echo Result:
"C:\Program Files (x86)\com0com\setupc.exe" list
echo.
pause
