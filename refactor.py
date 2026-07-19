import os
import re

template_dir = r"c:\Users\riadh\Desktop\srv-t\backend\templates"

for root, _, files in os.walk(template_dir):
    for file in files:
        if file.endswith(".html"):
            filepath = os.path.join(root, file)
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            
            new_content = content.replace('class="panel', 'class="section')
            new_content = new_content.replace('panel__', 'section__')
            new_content = re.sub(r'class="([^"]*)\bpanel\b', r'class="\1section', new_content)
            
            if new_content != content:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                print(f"Updated {filepath}")
