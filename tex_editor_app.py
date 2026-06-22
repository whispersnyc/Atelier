"""MR Texture Editor - standalone desktop launcher (pywebview over the local server).
Build:  python -m PyInstaller --noconfirm --onefile --windowed --name MRTextureEditor \
            --hidden-import img_data --collect-all webview tex_editor_app.py
Run MRTextureEditor.exe from the project root (next to Tools/, _work/, usmap/, mr_config.json)."""
import threading, time, socketserver
import tex_app

def _serve():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    socketserver.ThreadingTCPServer(("127.0.0.1", tex_app.PORT), tex_app.H).serve_forever()

def main():
    threading.Thread(target=_serve, daemon=True).start()
    time.sleep(0.8)
    try:
        import webview
        webview.create_window("MR Texture Editor", f"http://127.0.0.1:{tex_app.PORT}", width=1280, height=860)
        webview.start()
    except Exception:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{tex_app.PORT}")
        while True:
            time.sleep(3600)

if __name__ == "__main__":
    main()
