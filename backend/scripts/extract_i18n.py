#!/usr/bin/env python3
import os
import json
import re
from pathlib import Path
from html.parser import HTMLParser

class I18nExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.strings = {}
        self.output = []
        self.in_script_or_style = False

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.in_script_or_style = True
        
        # reconstruct tag
        attr_str = "".join([f' {k}="{v}"' if v is not None else f' {k}' for k, v in attrs])
        self.output.append(f"<{tag}{attr_str}>")

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.in_script_or_style = False
        self.output.append(f"</{tag}>")

    def handle_data(self, data):
        stripped = data.strip()
        
        # Skip empty, script/style, or jinja blocks
        if not self.in_script_or_style and stripped and not stripped.startswith("{%") and not stripped.startswith("{{"):
            # Skip pure punctuation or numbers (must contain letters)
            if re.search(r'[a-zA-Z]', stripped):
                # Generate key from the string
                key = re.sub(r'[^a-z0-9]+', '_', stripped.lower()).strip('_')
                if not key:
                    key = "text"
                
                # Truncate key if too long
                key = key[:40]
                
                # Handle duplicates with different text
                original_key = key
                counter = 1
                while key in self.strings and self.strings[key] != stripped:
                    key = f"{original_key}_{counter}"
                    counter += 1
                    
                self.strings[key] = stripped
                
                # Replace in data (careful to preserve surrounding whitespace)
                replacement = f"{{{{ _('{key}') }}}}"
                data = data.replace(stripped, replacement)

        self.output.append(data)
        
    def handle_entityref(self, name):
        self.output.append(f"&{name};")
        
    def handle_charref(self, name):
        self.output.append(f"&#{name};")
        
    def handle_comment(self, data):
        self.output.append(f"<!--{data}-->")
        
    def handle_decl(self, decl):
        self.output.append(f"<!{decl}>")
        
    def handle_pi(self, data):
        self.output.append(f"<?{data}>")

def process_file(filepath, strings_dict):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
        
    extractor = I18nExtractor()
    extractor.strings = strings_dict
    extractor.feed(content)
    
    new_content = "".join(extractor.output)
    
    if content != new_content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"Updated: {filepath}")

def main():
    base_dir = Path(__file__).resolve().parent.parent
    templates_dir = base_dir / "templates"
    locales_dir = base_dir / "locales"
    
    en_json_path = locales_dir / "en.json"
    
    strings = {}
    if en_json_path.exists():
        with open(en_json_path, 'r', encoding='utf-8') as f:
            try:
                strings = json.load(f)
            except:
                pass
                
    for root, dirs, files in os.walk(templates_dir):
        # Skip plugins as per requirement
        if "plugins" in Path(root).parts:
            continue
            
        for file in files:
            if file.endswith('.html'):
                process_file(os.path.join(root, file), strings)
                
    with open(en_json_path, 'w', encoding='utf-8') as f:
        json.dump(strings, f, indent=2, ensure_ascii=False)
    print(f"Done. Extracted {len(strings)} strings to {en_json_path}")

if __name__ == "__main__":
    main()
