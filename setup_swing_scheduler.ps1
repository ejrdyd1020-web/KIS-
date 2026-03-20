# setup_swing_scheduler.ps1
# ─────────────────────────────────────────────────────────────
# 스윙 자동매매 Windows Task Scheduler 등록 스크립트
# 실행: PowerShell을 관리자 권한으로 열고 아래 명령 실행
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
#   .\setup_swing_scheduler.ps1
# ─────────────────────────────────────────────────────────────

$ProjectDir = "C:\Users\홍윤석\AppData\Roaming\Claude\kis_autotrader"
$PythonExe  = "python"   # venv 사용 시 경로 수정

# ─────────────────────────────────────────────────────────────
# 작업 1: 스윙 장전 스캔 (08:50 평일)
# premarket.py에서 스윙 후보 스캔 → watchlist_swing.json 생성
# ─────────────────────────────────────────────────────────────
$Action1   = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "$ProjectDir\premarket.py --mode swing" `
    -WorkingDirectory $ProjectDir

$Trigger1  = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At "08:50"

$Settings1 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 8) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName   "KIS_Swing_Premarket" `
    -Action     $Action1 `
    -Trigger    $Trigger1 `
    -Settings   $Settings1 `
    -RunLevel   Highest `
    -Force

Write-Host "[OK] KIS_Swing_Premarket 등록 완료 (08:50)" -ForegroundColor Green

# ─────────────────────────────────────────────────────────────
# 작업 2: 스윙 메인 실행 (09:00 평일)
# swing_main.py → 09:00 진입 / 15:30 자동 종료
# ─────────────────────────────────────────────────────────────
$Action2   = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "$ProjectDir\swing\swing_main.py" `
    -WorkingDirectory $ProjectDir

$Trigger2  = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At "09:00"

$Settings2 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 7) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName   "KIS_Swing_Main" `
    -Action     $Action2 `
    -Trigger    $Trigger2 `
    -Settings   $Settings2 `
    -RunLevel   Highest `
    -Force

Write-Host "[OK] KIS_Swing_Main 등록 완료 (09:00)" -ForegroundColor Green

# ─────────────────────────────────────────────────────────────
# 작업 3: 장 마감 후 포지션 점검 (15:35 평일)
# 당일 청산 여부 확인, positions_swing.json 정합성 체크
# ─────────────────────────────────────────────────────────────
$Action3   = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "$ProjectDir\swing\swing_close_check.py" `
    -WorkingDirectory $ProjectDir

$Trigger3  = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At "15:35"

$Settings3 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName   "KIS_Swing_CloseCheck" `
    -Action     $Action3 `
    -Trigger    $Trigger3 `
    -Settings   $Settings3 `
    -RunLevel   Highest `
    -Force

Write-Host "[OK] KIS_Swing_CloseCheck 등록 완료 (15:35)" -ForegroundColor Green

# ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== 스윙 스케줄러 등록 완료 ===" -ForegroundColor Cyan
Write-Host "  KIS_Swing_Premarket  : 08:50 (평일) - 후보 스캔"
Write-Host "  KIS_Swing_Main       : 09:00 (평일) - 자동매매"
Write-Host "  KIS_Swing_CloseCheck : 15:35 (평일) - 마감 점검"
Write-Host ""
Write-Host "[참고] 기존 단타 스케줄러와 독립적으로 동작합니다." -ForegroundColor Yellow
Write-Host "  단타: KIS_Premarket (08:50), KIS_AutoTrader (09:00)"
Write-Host "  스윙: KIS_Swing_Premarket (08:50), KIS_Swing_Main (09:00)"
Write-Host ""
Write-Host "등록된 전체 KIS 작업 목록:"
Get-ScheduledTask | Where-Object {$_.TaskName -like "KIS_*"} | 
    Select-Object TaskName, State | Format-Table -AutoSize
