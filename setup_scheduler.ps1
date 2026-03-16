# KIS 자동매매 - 작업 스케줄러 등록 스크립트
# 관리자 권한으로 실행 필요

$pythonPath  = "C:\Users\홍윤석\AppData\Local\Python\bin\python.exe"
$scriptPath  = "C:\Users\홍윤석\AppData\Roaming\Claude\kis_autotrader\run_forever.py"
$workDir     = "C:\Users\홍윤석\AppData\Roaming\Claude\kis_autotrader"
$taskName    = "KIS_AutoTrader"
$userName    = $env:USERNAME

$action   = New-ScheduledTaskAction -Execute $pythonPath -Argument $scriptPath -WorkingDirectory $workDir
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $userName
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force

Write-Host ""
Write-Host "==== 등록 완료 ====" -ForegroundColor Green
Write-Host "작업명  : $taskName"
Write-Host "실행파일: $pythonPath"
Write-Host "스크립트: $scriptPath"
Write-Host "실행시점: 로그온 시 자동 실행"
Write-Host ""
Write-Host "확인하려면: 작업 스케줄러 앱 > KIS_AutoTrader 검색"
Write-Host ""
Read-Host "엔터를 누르면 창이 닫힙니다"
