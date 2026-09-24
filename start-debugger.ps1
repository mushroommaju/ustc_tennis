# Keep this terminal open. Open only the target mini-program after startup.
# This debugger uses the previously approved Frida runtime injection.
$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot 'work\runtime-validation\WMPFDebugger')
try {
    & node 'node_modules/ts-node/dist/bin.js' --compiler-options '{"module":"CommonJS"}' 'src/index.ts'
} finally {
    Pop-Location
}
