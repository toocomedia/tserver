import os
from jinja2 import Environment, FileSystemLoader

env = Environment(loader=FileSystemLoader(r"c:\Users\riadh\Desktop\srv-t\backend\templates"))

count = 0
errors = 0
for root, _, files in os.walk(r"c:\Users\riadh\Desktop\srv-t\backend\templates"):
    for file in files:
        if file.endswith(".html"):
            path = os.path.relpath(os.path.join(root, file), r"c:\Users\riadh\Desktop\srv-t\backend\templates")
            try:
                env.get_template(path.replace('\\', '/'))
                count += 1
            except Exception as e:
                print(f"Error in {path}: {e}")
                errors += 1

print(f"Checked {count} templates. Found {errors} errors.")
