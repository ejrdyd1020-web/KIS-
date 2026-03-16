@echo off
chcp 65001 >nul
echo KIS 자동매매 작업 스케줄러 등록 중...
echo.

powershell -ExecutionPolicy Bypass -Command ^
"$action = New-ScheduledTaskAction -Execute 'C:\Users\홍윤석\AppData\Local\Python\bin\python.exe' -Argument 'C:\Users\홍윤석\AppData\Roaming\Claude\kis_autotrader\run_forever.py' -WorkingDirectory 'C:\Users\홍윤석\AppData\Roaming\Claude\kis_autotrader'; $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME; $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5); Register-ScheduledTask -TaskName 'KIS_AutoTrader' -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force; Write-Host '등록 완료!' -ForegroundColor Green"

echo.
if %errorlevel%==0 (
    echo 성공! 다음 로그인부터 자동 실행됩니다.
) else (
    echo 실패. 관리자 권한으로 다시 실행해주세요.
)
echo.
pause
