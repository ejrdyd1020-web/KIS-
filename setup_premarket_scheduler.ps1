# ============================================================
#  setup_premarket_scheduler.ps1
#  premarket.py 를 매일 08:50 에 자동 실행 등록
#  PowerShell 관리자 권한으로 실행 필요
# ============================================================

$projectPath = "C:\Users\홍윤석\AppData\Roaming\Claude\kis_autotrader"
$pythonExe   = "python"

# 08:50 트리거 (월~금)
$trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At "08:50AM"

$action = New-ScheduledTaskAction `
    -Execute $pythonExe `
    -Argument "premarket.py" `
    -WorkingDirectory $projectPath

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName "KIS_Premarket" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Force

Write-Host "✅ KIS_Premarket 스케줄러 등록 완료 (매일 08:50 자동 실행)" -ForegroundColor Green
Write-Host "확인: Get-ScheduledTaskInfo -TaskName 'KIS_Premarket'" -ForegroundColor Cyan
