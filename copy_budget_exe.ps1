$source = 'D:\budgettest\dist'
$target = 'C:\Users\lucaa\OneDrive\Documents\Money Manager Luca_Database\budget_luca_app'

New-Item -ItemType Directory -Path $target -Force | Out-Null
Get-ChildItem -Path $source -Filter *.exe | ForEach-Object {
    Copy-Item -Path $_.FullName -Destination $target -Force
}

Write-Host "Copied executables from $source to $target"
