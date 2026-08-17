# AI Assistant — Integration Guide for SRV / Barq Panel

This guide explains how to integrate the **AI Assistant** into any page, wizard, code editor, or log viewer across the panel.

---

## 1. Quick Start

Because `ai-helper.js` and `ai-helper.css` are loaded globally in `layout.html`, **`window.AiHelper` is available on every page out-of-the-box.**

You can trigger the AI Assistant in two ways:
1. **Declarative HTML data attributes** (zero JS code needed)
2. **Programmatic JavaScript API** (`window.AiHelper`)

---

## 2. Declarative HTML Triggers (Zero Code)

### A. Pre-filled Prompt Button
Add `data-ai-prompt` to any button or link. Clicking it opens the AI Assistant drawer and immediately sends the prompt:

```html
<button type="button" 
        class="btn btn--sm btn--secondary" 
        data-ai-prompt="Explain how to configure environment variables for a Node.js Express app.">
  <i data-lucide="sparkles"></i> Ask AI
</button>
```

### B. "Explain Error" Button for Logs / Terminals
Point `data-ai-explain-error` to a CSS selector (e.g. a `<pre>` log container). Clicking it extracts the error text, trims it, and opens the AI Assistant to diagnose the failure:

```html
<!-- Deployment or terminal log container -->
<pre id="build-logs">{{ deployment.log_output }}</pre>

<!-- AI Explainer button -->
<button type="button" 
        class="btn btn--sm btn--secondary" 
        data-ai-explain-error="#build-logs"
        data-ai-context="Railpack Build Failure">
  <i data-lucide="sparkles"></i> Explain Error with AI
</button>
```

---

## 3. Programmatic JavaScript API (`window.AiHelper`)

### A. Open the Chat Drawer with Context
```javascript
AiHelper.open({
  title: "Apps Engine Helper",
  context: "Repository: github.com/user/my-repo | Framework: Python Flask | Port: 5000",
  initialPrompt: "What environment variables does Flask usually require for production?"
});
```

### B. Direct Streaming Call (Custom UI Integration)
If you want to render the AI output inside your own custom modal or page section:

```javascript
AiHelper.ask("Generate a starter Nginx reverse proxy configuration for port 8080", {
  context: "Nginx Config Generator",
  onChunk: function(token) {
    // Called for every streaming token
    outputElement.textContent += token;
  },
  onComplete: function(fullText) {
    console.log("AI finished response:", fullText);
  },
  onError: function(err) {
    console.error("AI request error:", err);
  }
});
```

### C. Shortcut: Explain an Error String
```javascript
const logContent = document.getElementById("deployment-logs").innerText;

AiHelper.explainError(logContent, {
  context: "Container App Deployment Error"
});
```

---

## 4. Integration Recipes for Key Panel Modules

### Recipe 1: Apps Engine (`railpack_apps`) Wizard
Add an **"AI Setup Assistant"** button inside `railpack_apps_create.html` (Configuration / Env step):

```html
<div class="card card--subtle p-md mb-md">
  <div class="d-flex align-items-center justify-content-between">
    <div>
      <h4 class="m-0"><i data-lucide="sparkles"></i> Need help configuring this app?</h4>
      <p class="text-muted text-sm m-0">Let AI suggest port numbers, database URLs, and required environment variables.</p>
    </div>
    <button type="button" class="btn btn--sm btn--primary" id="ai-wizard-suggest-btn">
      Suggest Config with AI
    </button>
  </div>
</div>

<script>
document.getElementById("ai-wizard-suggest-btn")?.addEventListener("click", function() {
  const repoUrl = document.querySelector("[name='repo_url']")?.value || "Unknown Repo";
  AiHelper.open({
    title: "App Setup Assistant",
    context: "Configuring new App from Repo: " + repoUrl,
    initialPrompt: "I am deploying this repository: " + repoUrl + ". What port and environment variables should I configure?"
  });
});
</script>
```

---

### Recipe 2: Deployment Log Viewer
Add an **"Explain Failure"** button next to failed build logs:

```html
{% if deployment.status == 'failed' %}
<div class="alert alert--danger d-flex justify-content-between align-items-center mb-md">
  <span><strong>Deployment Failed:</strong> Check logs below for error trace.</span>
  <button type="button" 
          class="btn btn--sm btn--danger" 
          data-ai-explain-error="#deployment-live-logs"
          data-ai-context="App: {{ app.name }} | Deployment #{{ deployment.id }}">
    <i data-lucide="sparkles"></i> Explain Error with AI
  </button>
</div>
{% endif %}
```

---

### Recipe 3: File Manager / Code Editor
Add an **"Ask AI to Fix / Format Code"** action in the File Manager editor toolbar:

```javascript
function askAiAboutCurrentFile() {
  const filePath = currentEditingFilePath;
  const fileContent = editorInstance.getValue();
  
  AiHelper.open({
    title: "Code Assistant — " + filePath,
    context: "File: " + filePath + "\n```\n" + fileContent.slice(0, 3000) + "\n```",
    initialPrompt: "Review this file for syntax errors or configuration issues."
  });
}
```

---

## 5. Structured Action Tags

The AI Assistant is instructed to format suggested values with structured tags:
* `[ACTION:SET_PORT:3000]`
* `[ACTION:SET_ENV:KEY=VALUE]`
* `[ACTION:RUN_CMD:docker-compose up -d]`

`ai-helper.js` automatically detects these tags in the stream and renders them as styled **"📋 Copy"** action buttons.
