/**
 * chat_codeview.js — Dedicated Code View Window & Inspector for AI Assistant.
 * Provides line numbering, syntax headers, line wrapping toggles, copy, and file export.
 */
(function () {
  "use strict";

  var EXT_MAP = {
    nginx: "nginx.conf",
    dockerfile: "Dockerfile",
    docker: "Dockerfile",
    compose: "docker-compose.yml",
    yaml: "config.yaml",
    yml: "config.yml",
    json: "config.json",
    bash: "script.sh",
    sh: "script.sh",
    shell: "script.sh",
    python: "main.py",
    py: "main.py",
    php: "index.php",
    javascript: "app.js",
    js: "app.js",
    typescript: "app.ts",
    ts: "app.ts",
    sql: "query.sql",
    html: "index.html",
    css: "style.css",
    ini: "config.ini",
    env: ".env",
    xml: "data.xml",
    markdown: "README.md",
    md: "README.md",
  };

  var AiHelperCodeView = {
    modalEl: null,
    linesEl: null,
    codeEl: null,
    langBadgeEl: null,
    filenameEl: null,
    linesCountEl: null,
    copyBtnEl: null,
    wrapBtnEl: null,
    downloadBtnEl: null,
    currentCode: "",
    currentLang: "text",
    currentFilename: "snippet.txt",
    isWrapped: false,

    init: function () {
      if (document.getElementById("ai-code-viewer-modal")) return;

      var modal = document.createElement("div");
      modal.id = "ai-code-viewer-modal";
      modal.className = "ai-code-viewer-modal";
      modal.innerHTML = [
        '<div class="ai-code-viewer-backdrop" id="ai-code-viewer-backdrop"></div>',
        '<div class="ai-code-viewer-container">',
        '  <div class="ai-code-viewer-header">',
        '    <div class="ai-code-viewer-meta">',
        '      <span class="ai-code-lang-pill" id="ai-code-view-lang">CODE</span>',
        '      <span class="ai-code-filename" id="ai-code-view-filename">snippet.txt</span>',
        '      <span class="ai-code-lines-badge" id="ai-code-view-lines-count">0 lines</span>',
        "    </div>",
        '    <div class="ai-code-viewer-actions">',
        '      <button type="button" class="ai-code-btn" id="ai-code-view-wrap-btn" title="Toggle line wrap">Wrap Lines</button>',
        '      <button type="button" class="ai-code-btn" id="ai-code-view-copy-btn" title="Copy entire code snippet"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg> Copy</button>',
        '      <button type="button" class="ai-code-btn" id="ai-code-view-download-btn" title="Download snippet file"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg> Download</button>',
        '      <button type="button" class="ai-code-btn-close" id="ai-code-view-close-btn" title="Close viewer (Esc)">✕</button>',
        "    </div>",
        "  </div>",
        '  <div class="ai-code-viewer-body" id="ai-code-viewer-body">',
        '    <div class="ai-code-lines-gutter" id="ai-code-view-lines"></div>',
        '    <div class="ai-code-view-content">',
        '      <pre class="ai-code-view-pre"><code class="ai-code-view-code" id="ai-code-view-code"></code></pre>',
        "    </div>",
        "  </div>",
        "</div>",
      ].join("\n");

      document.body.appendChild(modal);

      this.modalEl = modal;
      this.linesEl = document.getElementById("ai-code-view-lines");
      this.codeEl = document.getElementById("ai-code-view-code");
      this.langBadgeEl = document.getElementById("ai-code-view-lang");
      this.filenameEl = document.getElementById("ai-code-view-filename");
      this.linesCountEl = document.getElementById("ai-code-view-lines-count");
      this.copyBtnEl = document.getElementById("ai-code-view-copy-btn");
      this.wrapBtnEl = document.getElementById("ai-code-view-wrap-btn");
      this.downloadBtnEl = document.getElementById("ai-code-view-download-btn");

      var self = this;
      document.getElementById("ai-code-viewer-backdrop").addEventListener("click", function () { self.close(); });
      document.getElementById("ai-code-view-close-btn").addEventListener("click", function () { self.close(); });
      this.copyBtnEl.addEventListener("click", function () { self.copy(); });
      this.wrapBtnEl.addEventListener("click", function () { self.toggleWrap(); });
      this.downloadBtnEl.addEventListener("click", function () { self.download(); });

      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && self.isOpen()) {
          self.close();
        }
      });
    },

    isOpen: function () {
      return this.modalEl && this.modalEl.classList.contains("open");
    },

    open: function (code, lang, customFilename) {
      this.init();
      this.currentCode = code || "";
      this.currentLang = (lang || "text").toLowerCase().trim();

      var defaultFilename = EXT_MAP[this.currentLang] || ("snippet." + (this.currentLang || "txt"));
      this.currentFilename = customFilename || defaultFilename;

      if (this.langBadgeEl) this.langBadgeEl.textContent = (this.currentLang || "CODE").toUpperCase();
      if (this.filenameEl) this.filenameEl.textContent = this.currentFilename;

      var lines = this.currentCode.split("\n");
      var lineCount = lines.length;
      if (this.linesCountEl) {
        this.linesCountEl.textContent = lineCount + (lineCount === 1 ? " line" : " lines");
      }

      // Generate Line Numbers
      var gutterHtml = [];
      for (var i = 1; i <= lineCount; i++) {
        gutterHtml.push('<span class="ai-line-num">' + i + "</span>");
      }
      if (this.linesEl) this.linesEl.innerHTML = gutterHtml.join("");

      // Populate Code Content
      if (this.codeEl) {
        this.codeEl.textContent = this.currentCode;
      }

      this.isWrapped = false;
      var body = document.getElementById("ai-code-viewer-body");
      if (body) body.classList.remove("ai-code-wrapped");
      if (this.wrapBtnEl) this.wrapBtnEl.textContent = "Wrap Lines";

      this.modalEl.classList.add("open");
    },

    close: function () {
      if (this.modalEl) this.modalEl.classList.remove("open");
    },

    toggleWrap: function () {
      this.isWrapped = !this.isWrapped;
      var body = document.getElementById("ai-code-viewer-body");
      if (body) {
        if (this.isWrapped) body.classList.add("ai-code-wrapped");
        else body.classList.remove("ai-code-wrapped");
      }
      if (this.wrapBtnEl) {
        this.wrapBtnEl.textContent = this.isWrapped ? "Unwrap" : "Wrap Lines";
      }
    },

    copy: function () {
      var self = this;
      if (window.AiHelperActions) {
        window.AiHelperActions.copyToClipboard(this.currentCode, this.copyBtnEl);
      } else if (navigator.clipboard) {
        navigator.clipboard.writeText(this.currentCode).then(function () {
          var orig = self.copyBtnEl.textContent;
          self.copyBtnEl.textContent = "Copied!";
          setTimeout(function () { self.copyBtnEl.textContent = orig; }, 1500);
        });
      }
    },

    download: function () {
      if (!this.currentCode) return;
      var blob = new Blob([this.currentCode], { type: "text/plain;charset=utf-8" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = this.currentFilename || "snippet.txt";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    },
  };

  window.AiHelperCodeView = AiHelperCodeView;
})();
