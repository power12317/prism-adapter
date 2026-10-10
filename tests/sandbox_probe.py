"""Fail if Chromium's actual namespace or seccomp sandbox is disabled."""
import os
from playwright.sync_api import sync_playwright

assert os.geteuid() == 10001
with sync_playwright() as p:
    with p.chromium.launch(executable_path=os.environ['PRISM_ADAPTER_CHROME'],
                           headless=True, chromium_sandbox=True) as browser:
        page = browser.new_page()
        page.goto('chrome://sandbox')
        text = page.locator('body').inner_text()
        print(text)
        for name in ('Namespace sandbox', 'Seccomp-BPF sandbox'):
            assert any(name.lower() in line.lower() and 'yes' in line.lower()
                       for line in text.splitlines()), (name, text)
