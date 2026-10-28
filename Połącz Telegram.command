#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if /usr/bin/python3 -c 'from connect import CONFIG; raise SystemExit(0 if CONFIG.exists() else 1)'; then
    /usr/bin/python3 connect.py run
else
    /usr/bin/python3 connect.py configure && /usr/bin/python3 connect.py run
fi
read -r "reply?Naciśnij Enter, aby zamknąć okno. "
