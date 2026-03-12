@echo off
echo Building Tailwind CSS...
cd /d "%~dp0"
tailwindcss.exe -c tailwind.config.js -i tailwind-input.css -o tailwind.css --minify
echo Done! tailwind.css has been updated.
pause
