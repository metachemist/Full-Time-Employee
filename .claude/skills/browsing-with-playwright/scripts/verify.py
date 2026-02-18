#!/usr/bin/env python3
"""Verify Playwright MCP server is running and accessible."""
import sys
import urllib.request
import urllib.error

PORT = 8808
URL = f"http://localhost:{PORT}/"

def main():
    try:
        urllib.request.urlopen(URL, timeout=3)
        print("✓ Playwright MCP server running")
        sys.exit(0)
    except urllib.error.HTTPError:
        # Any HTTP response (even 4xx) means the server is up
        print("✓ Playwright MCP server running")
        sys.exit(0)
    except (urllib.error.URLError, OSError):
        print("✗ Server not responding. Run: bash scripts/start-server.sh")
        sys.exit(1)

if __name__ == "__main__":
    main()
