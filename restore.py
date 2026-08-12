import json
import os

with open(r"c:\Users\riadh\Desktop\srv-t\backend\locales\en.json", "r", encoding="utf-8") as f:
    strings = json.load(f)

templates_dir = r"c:\Users\riadh\Desktop\srv-t\backend\templates"
count = 0

for root, _, files in os.walk(templates_dir):
    for f in files:
        if f.endswith('.html'):
            p = os.path.join(root, f)
            with open(p, "r", encoding="utf-8") as file:
                content = file.read()
            
            changed = False
            for key, original in strings.items():
                if original.startswith("{#") or original.startswith("{%"):
                    target = f"{{{{ _('{key}') }}}}"
                    if target in content:
                        content = content.replace(target, original)
                        changed = True
            
            if changed:
                with open(p, "w", encoding="utf-8") as file:
                    file.write(content)
                count += 1

print(f"Restored Jinja blocks in {count} files.")
