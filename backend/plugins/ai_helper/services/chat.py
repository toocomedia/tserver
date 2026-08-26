"""
services/chat.py — Multi-turn streaming chat pipeline with tool calling, DSML support, and session auto-tracking.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.ai_helper import AiChatMessage
from plugins.ai_helper import engine, permissions, prompts, tools
from plugins.ai_helper.tools import app_setup as app_setup_tools
from plugins.ai_helper.services.providers import decrypt_key, get_active_provider, get_provider
from plugins.ai_helper.services.secrets_consent import check_consent_phrase, is_secrets_allowed
from plugins.ai_helper.services.sessions import generate_title_from_prompt, get_or_create_session
from plugins.ai_helper.services import setup_handoff, setup_plan_builder, visible_output
missing_plan_message = setup_handoff.missing_plan_message

logger = logging.getLogger(__name__)

# Sentinel prefix used to pass tool activity events through the async generator.
# The router detects this prefix and wraps as type=tool_activity SSE events.
_ACTIVITY_PREFIX = "\x00ACTIVITY\x00"

# Friendly labels and icon identifiers for each tool name
_TOOL_LABELS = {
    "get_domains_and_ssl": ("globe", "Checking SSL & domain configuration"),
    "get_reverse_proxy_routes": ("route", "Reading Nginx proxy routes"),
    "get_dns_records": ("list", "Querying DNS records"),
    "get_apps_overview": ("box", "Listing hosted apps"),
    "get_app_logs": ("file-text", "Fetching deployment logs"),
    "get_databases_overview": ("database", "Reading database overview"),
    "list_website_directory": ("folder", "Scanning directory"),
    "read_website_file": ("file", "Reading file"),
    "fetch_web_documentation": ("book-open", "Reading documentation"),
    "search_web_docs": ("search", "Searching web documentation"),
    "search_docker_hub": ("box", "Searching Docker Hub"),
    "search_app_source": ("search", "Searching repository source code"),
    "read_app_source_file": ("file", "Reading source file"),
    "inspect_official_image": ("search", "Inspecting official image registry"),
    "inspect_app_source": ("search", "Inspecting repository & Compose services"),
    "get_app_engine_capabilities": ("layers", "Reading App Engine capabilities"),
    "get_app_engine_diagnostics": ("activity", "Collecting runtime diagnostics"),
    "propose_app_install": ("layers", "Generating deployment plan"),
    "propose_stack_install": ("layers", "Synthesizing stack deployment plan"),
    "propose_official_stack_install": ("layers", "Synthesizing stack deployment plan"),
    "propose_container_app_patch": ("layers", "Creating container patch plan"),
    "ai_reasoning": ("cpu", "Evaluating architecture & stack dependencies"),
    "ai_planning": ("layers", "Finalizing reviewed setup plan"),
}


def _extract_explicit_setup_domain(*texts: str | None) -> str:
    """Extract domain if explicitly indicated by domain/host keywords."""
    joined = "\n".join(text or "" for text in texts)
    for pattern in (
        r"\b(?:domain|host(?:name)?|fqdn|site)\s*[:=]?\s*([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)",
        r"(?:for|to|at)\s+([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)",
    ):
        match = re.search(pattern, joined, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip().lower()
            ignored = {"github.com", "www.github.com", "gitlab.com", "bitbucket.org", "docker.io", "hub.docker.com"}
            if candidate not in ignored and not candidate.endswith((".yml", ".yaml", ".json", ".js", ".ts", ".py", ".md", ".txt", ".sh", ".html", ".css", ".png", ".jpg", ".jpeg", ".svg")):
                return candidate
    explicit = re.search(r"@domain:([a-z0-9.-]+)", joined, re.IGNORECASE)
    if explicit:
        candidate = explicit.group(1).strip().lower()
        ignored = {"github.com", "www.github.com", "gitlab.com", "bitbucket.org", "docker.io", "hub.docker.com"}
        if candidate not in ignored and not candidate.endswith((".yml", ".yaml", ".json", ".js", ".ts", ".py", ".md", ".txt", ".sh", ".html", ".css", ".png", ".jpg", ".jpeg", ".svg")):
            return candidate
    return ""


def _extract_setup_domain(*texts: str | None) -> str:
    """Best-effort target domain extraction for server-owned setup fallback."""
    explicit = _extract_explicit_setup_domain(*texts)
    if explicit:
        return explicit
    joined = "\n".join(text or "" for text in texts)
    # Strip out email addresses first to prevent emails (e.g. admin@tooco.net) from matching domain patterns
    cleaned = re.sub(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", "", joined)
    domain_pattern = r"([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)"
    ignored = {"github.com", "www.github.com", "gitlab.com", "bitbucket.org", "docker.io", "hub.docker.com"}
    for match in re.finditer(rf"\b{domain_pattern}\b", cleaned, re.IGNORECASE):
        candidate = match.group(1).strip().lower()
        if candidate not in ignored and not candidate.endswith((".yml", ".yaml", ".json", ".js", ".ts", ".py", ".md", ".txt", ".sh", ".html", ".css", ".png", ".jpg", ".jpeg", ".svg")):
            return candidate
    return ""


def _extract_setup_source(*texts: str | None) -> tuple[str, str, str]:
    """Returns (source_type, repository_url, image_reference) extracted from user input/context."""
    joined = "\n".join(text or "" for text in texts)
    reg_img_match = re.search(r"deployment_method\s*:\s*registry_image(?::([^\s\n]+))?", joined, re.IGNORECASE)
    if reg_img_match and reg_img_match.group(1):
        return "image", "", reg_img_match.group(1).strip()
    git_match = re.search(r"(https?://[^\s\"'<>]*(?:github|gitlab|bitbucket)[^\s\"'<>]*|git@[^\s\"'<>]+|https?://[^\s\"'<>]+\.git)", joined, re.IGNORECASE)
    if git_match:
        return "git", git_match.group(1).strip(), ""
    img_match = re.search(r"(?:image[:\s]+)?([a-z0-9_.-]+/[a-z0-9_.-]+(?::[a-z0-9_.-]+)?|[a-z0-9_-]+:[a-z0-9_.-]+)", joined, re.IGNORECASE)
    if img_match:
        val = img_match.group(1).strip()
        if not val.startswith("http"):
            return "image", "", val
    return "git", "", ""


def _extract_app_id(*texts: str | None) -> int | None:
    """Extract App Engine app ID from user message, context key, or context text."""
    joined = "\n".join(text or "" for text in texts)
    m = re.search(r"(?:app_id[:=\s]+|container:|app:|\bID\s*#?\s*)(\d+)\b", joined, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None



def _activity_event(tool_name: str, status: str, args: dict | None = None) -> str:
    """Builds an activity sentinel string to yield from the generator."""
    import json as _json
    icon, label = _TOOL_LABELS.get(tool_name, ("tool", tool_name.replace("_", " ").title()))
    # Add context detail from args
    detail = ""
    if args:
        for key in ("query", "url", "image_reference", "target_id", "domain", "domain_name", "app_id", "file_path"):
            if args.get(key):
                detail = str(args[key])
                break
    payload = _json.dumps({
        "tool": tool_name,
        "status": status,   # "start" | "done" | "error"
        "icon": icon,
        "label": label,
        "detail": detail,
    })
    return _ACTIVITY_PREFIX + payload


def _setup_plan_id(tool_name: str, tool_output: Dict[str, Any]) -> str | None:
    """Return only a server-created, safe wizard handoff plan identifier."""
    if tool_name not in {"propose_app_install", "propose_stack_install", "propose_official_stack_install", "propose_container_app_patch"} or tool_output.get("status") != "ok":
        return None
    plan_id = tool_output.get("plan_id")
    if isinstance(plan_id, str) and re.fullmatch(r"plan_[0-9a-f]{16}", plan_id):
        return plan_id
    return None


def _extract_text_tool_calls(step_content: str) -> List[Dict[str, Any]]:
    """Extracts DeepSeek DSML or XML pseudo tool calls emitted in raw text."""
    if not step_content:
        return []
    tool_calls = []

    # 1. DeepSeek DSML format: <｜｜DSML｜｜invoke name="fn_name">...<｜｜DSML｜｜parameter name="target_id"...>val</｜｜DSML｜｜parameter>...</｜｜DSML｜｜invoke>
    dsml_invokes = re.finditer(
        r"<[｜|]{1,2}DSML[｜|]{1,2}invoke\s+name=[\"']?([a-zA-Z0-9_]+)[\"']?[^>]*>(.*?)</[｜|]{1,2}DSML[｜|]{1,2}invoke>",
        step_content,
        re.DOTALL | re.IGNORECASE,
    )
    for m in dsml_invokes:
        fn_name = m.group(1)
        body = m.group(2)
        params = {}
        for pm in re.finditer(
            r"<[｜|]{1,2}DSML[｜|]{1,2}parameter\s+name=[\"']?([a-zA-Z0-9_]+)[\"']?[^>]*>(.*?)</[｜|]{1,2}DSML[｜|]{1,2}parameter>",
            body,
            re.DOTALL | re.IGNORECASE,
        ):
            params[pm.group(1).strip()] = pm.group(2).strip()
        tool_calls.append({"id": f"call_{len(tool_calls)}_{fn_name}", "name": fn_name, "arguments": params})

    if tool_calls:
        return tool_calls

    # 2. Standard XML: <function=fn_name>...</function> or <invoke name="fn_name">...</invoke>
    fn_matches = re.finditer(
        r"<(?:function=|invoke\s+name=[\"']?)([a-zA-Z0-9_]+)[\"']?[^>]*>(.*?)</(?:function|invoke)>",
        step_content,
        re.DOTALL | re.IGNORECASE,
    )
    for m in fn_matches:
        fn_name = m.group(1)
        body = m.group(2)
        params = {}
        for pm in re.finditer(
            r"<(?:parameter=|parameter\s+name=[\"']?)([a-zA-Z0-9_]+)[\"']?[^>]*>(.*?)</parameter>",
            body,
            re.DOTALL | re.IGNORECASE,
        ):
            params[pm.group(1).strip()] = pm.group(2).strip()
        tool_calls.append({"id": f"call_{len(tool_calls)}_{fn_name}", "name": fn_name, "arguments": params})

    return tool_calls


def _sanitize_history_content(
    text: str,
    *,
    allow_setup_action: bool = False,
    allow_sensitive_file_unlock: bool = False,
) -> str:
    """Removes unfulfilled XML/DSML pseudo tool calls and error banners from message history."""
    if not text:
        return ""
    if text.strip().startswith("[Error from AI Provider:") or text.strip().startswith("[Error:"):
        return ""
    clean = re.sub(r"<[｜|]{1,2}DSML[｜|]{1,2}[\s\S]*?</[｜|]{1,2}DSML[｜|]{1,2}[^>]*>", "", text, flags=re.IGNORECASE)
    clean = re.sub(r"<[｜|][\s\S]*?[｜|]>", "", clean)
    clean = re.sub(r"<tool_call>[\s\S]*?</tool_call>", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"<function=[a-zA-Z0-9_]+>[\s\S]*?</function>", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"<parameter=[a-zA-Z0-9_]+>[\s\S]*?</parameter>", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"<\/?(?:tool_call|function|parameter|invoke)[^>]*>", "", clean, flags=re.IGNORECASE)
    return visible_output.strip_hidden_reasoning(
        clean,
        allow_setup_action=allow_setup_action,
        allow_sensitive_file_unlock=allow_sensitive_file_unlock,
    ).strip()


def _flatten_tool_messages_for_text_generation(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Converts tool call messages into standard assistant/user text messages for final non-tool streaming.
    Prevents provider 400 errors (e.g. Gemini 'Function call missing thought_signature' or missing tool defs).
    """
    flattened: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            content = msg.get("content") or ""
            flattened.append({"role": "assistant", "content": content or "Inspected configuration."})
        elif role == "tool":
            fn = msg.get("name") or "tool"
            raw_content = msg.get("content") or ""
            flattened.append({"role": "user", "content": f"[Tool Result for {fn}]:\n{raw_content}"})
        else:
            flattened.append(msg)
    return flattened


def _normalize_messages_for_llm(messages: List[Dict[str, Any]], provider_type: str) -> List[Dict[str, Any]]:
    """
    Validates and normalizes conversation messages to guarantee strict compliance
    with OpenAI and Anthropic tool calling schemas:
    1. Ensures every assistant message with 'tool_calls' is followed immediately and exclusively
       by matching 'role: tool' messages for each tool_call_id (OpenAI protocol).
    2. If any tool call ID lacks a response, injects a synthetic tool response error message
       so the AI provider never returns a 400 Bad Request error.
    3. Converts orphaned 'role: tool' messages to user context messages.
    """
    if not messages:
        return []

    normalized: List[Dict[str, Any]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg.get("role")

        if role == "assistant" and msg.get("tool_calls"):
            tool_calls = msg.get("tool_calls") or []
            normalized.append(msg)
            i += 1

            if provider_type != "anthropic":
                existing_tool_responses: Dict[str, Dict[str, Any]] = {}
                while i < len(messages) and messages[i].get("role") == "tool":
                    t_msg = messages[i]
                    t_id = t_msg.get("tool_call_id")
                    if t_id:
                        existing_tool_responses[t_id] = t_msg
                    i += 1

                for idx, tc in enumerate(tool_calls):
                    tc_id = tc.get("id") or f"call_{idx}"
                    if tc_id in existing_tool_responses:
                        normalized.append(existing_tool_responses[tc_id])
                    else:
                        fn_name = (tc.get("function") or {}).get("name") or tc.get("name") or "tool"
                        normalized.append({
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "name": fn_name,
                            "content": json.dumps({"status": "error", "message": "Tool execution was skipped or interrupted."}),
                        })
            continue

        elif role == "tool":
            # Orphaned tool message not directly following an assistant tool_calls message
            content_str = msg.get("content") or ""
            fn_name = msg.get("name") or "tool"
            normalized.append({
                "role": "user",
                "content": f"[Tool Result for {fn_name}]: {content_str}",
            })
            i += 1
            continue

        else:
            normalized.append(msg)
            i += 1

    return normalized


async def stream_ai_chat(
    db: AsyncSession,
    session_id: str,
    user_message: str,
    context_key: Optional[str] = None,
    context_text: Optional[str] = None,
    provider_id: Optional[int] = None,
    model_name: Optional[str] = None,
    task_type: Optional[str] = "general",
    session_title: Optional[str] = None,
    user_id: Optional[int] = None,
) -> AsyncGenerator[str, None]:
    """
    Multi-turn streaming chat pipeline with tool calling support:
    1. Loads specified or active provider & decrypted API key.
    2. Builds modular system prompt (base + tools + action tags + context + custom rules).
    3. Loads previous conversation history strictly for this session and sanitizes history.
    4. Evaluates tool calling in a multi-turn loop if permissions permit.
    5. Executes requested panel tools and feeds results back to AI with strict schema compliance.
    6. Streams AI response chunks and persists the conversation.
    7. Updates or auto-titles the AiChatSession record.
    """
    active = None
    if provider_id:
        active = await get_provider(db, provider_id)
    if not active:
        active = await get_active_provider(db)

    if not active:
        yield "Error: No AI provider configured. Please add an AI provider in AI Assistant settings."
        return

    if not active.is_enabled:
        yield "Error: Active AI provider is currently disabled."
        return

    api_key = decrypt_key(active.api_key_encrypted)
    if not api_key:
        yield "Error: No API key configured for the active provider. Please configure an API key."
        return

    effective_model = model_name.strip() if model_name and model_name.strip() else active.model_name

    # Check secrets consent from user message
    check_consent_phrase(session_id, user_message)
    secrets_allowed = is_secrets_allowed(session_id)

    # Check permission policy
    policy = await permissions.get_or_create_policy(db)
    tools_enabled = (policy.global_mode != "disabled")

    trimmed_context = engine.trim_context_log(context_text or "")
    system_prompt = prompts.build_system_prompt(
        context=trimmed_context,
        custom_rules=active.custom_rules,
        include_tools_rules=tools_enabled,
        skill=task_type,
        secrets_allowed=secrets_allowed,
    )

    # Manage or create AiChatSession
    session_record = await get_or_create_session(
        db=db,
        session_id=session_id,
        title=session_title,
        task_type=task_type or "general",
        context_key=context_key,
        provider_id=active.id,
        model_name=effective_model,
    )

    # Auto-generate title on first message if default
    if session_record.title in ("New Chat", "", None):
        if session_title and session_title.strip():
            session_record.title = session_title.strip()
        else:
            session_record.title = generate_title_from_prompt(user_message)

    # Fetch recent conversation history strictly for this session (last 20 messages)
    stmt = (
        select(AiChatMessage)
        .where(AiChatMessage.session_id == session_id)
        .order_by(AiChatMessage.id.desc())
        .limit(20)
    )
    result = await db.execute(stmt)
    recent_records = list(reversed(result.scalars().all()))

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt}
    ]

    for record in recent_records:
        clean_history = _sanitize_history_content(record.content)
        if clean_history:
            messages.append({"role": record.role, "content": clean_history})

    messages.append({"role": "user", "content": user_message})

    # Save user message to database
    user_record = AiChatMessage(
        session_id=session_id,
        role="user",
        content=user_message,
        context_key=context_key,
    )
    db.add(user_record)
    session_record.message_count += 1
    session_record.updated_at = datetime.now(tz=timezone.utc)
    await db.commit()

    # Tool calling loop if enabled
    tool_was_executed = False
    setup_plan_id: str | None = None
    setup_plan_kind: str = "install"
    sensitive_file_blocked = False
    setup_plan_required = tools_enabled and setup_handoff.requires_reviewed_plan(task_type)
    setup_plan_errors: List[str] = []
    setup_stack_correction_allowed = False
    setup_stack_correction_prompted = False
    setup_stack_correction_reason = ""
    setup_plan_tool_prompted = False
    setup_server_plan_attempted = False
    setup_documentation_failed = False
    setup_source_result: Dict[str, Any] | None = None
    setup_provider_choice: Dict[str, Any] | None = None
    setup_interview_options_str: str = ""
    # Multi-turn setup context extraction with session persistence
    history_texts = [r.content for r in recent_records]
    explicit_domain = _extract_explicit_setup_domain(user_message, context_text)
    if explicit_domain:
        setup_target_domain = explicit_domain
    elif session_record and session_record.target_domain:
        setup_target_domain = session_record.target_domain
    else:
        setup_target_domain = (
            _extract_setup_domain(user_message, context_text)
            or _extract_setup_domain(*history_texts)
        )
    if setup_target_domain and session_record and session_record.target_domain != setup_target_domain:
        session_record.target_domain = setup_target_domain

    stype, repo, img = _extract_setup_source(user_message, context_text)
    if not repo and session_record and session_record.repository_url:
        repo = session_record.repository_url
        stype = "git"
    if not img and session_record and session_record.image_reference:
        img = session_record.image_reference
        stype = "image"
    if not repo and not img:
        stype_h, repo_h, img_h = _extract_setup_source(*history_texts)
        if repo_h:
            repo, stype = repo_h, "git"
        elif img_h:
            img, stype = img_h, "image"

    if repo and session_record and session_record.repository_url != repo:
        session_record.repository_url = repo
    if img and session_record and session_record.image_reference != img:
        session_record.image_reference = img
    await db.commit()

    app_id = _extract_app_id(user_message, context_key, context_text)
    is_app_diag = setup_handoff.is_diagnostic_task(task_type, has_app_id=bool(app_id))

    # 1. Fast 1-Turn Pre-Inspection for App Engine Setup:
    if tools_enabled and setup_plan_required and (repo or img) and not setup_source_result:
        yield _activity_event("inspect_app_source", "start", {"repository_url": repo, "image_reference": img})
        try:
            setup_source_result = await tools.execute_tool(
                db=db,
                tool_name="inspect_app_source",
                arguments={"source_type": stype, "repository_url": repo, "image_reference": img},
                session_id=session_id,
                user_id=user_id,
                secrets_allowed=secrets_allowed,
            )
            yield _activity_event("inspect_app_source", "done", {"repository_url": repo, "image_reference": img})
            if setup_source_result.get("status") == "ok":
                tool_was_executed = True
                from services.official_stacks.stack_synthesizer import requires_multi_container_stack
                needs_stack = requires_multi_container_stack(setup_source_result)
                if setup_handoff.is_setup_interview_pending(setup_source_result, user_message):
                    inspection = setup_source_result.get("inspection") if isinstance(setup_source_result.get("inspection"), dict) else setup_source_result
                    doc_ev = (inspection.get("documentation_evidence") or {}) if isinstance(inspection, dict) else {}
                    detected_imgs = list(doc_ev.get("detected_docker_images") or [])
                    image_rec = setup_source_result.get("official_image_recommendation") or (inspection.get("official_image_recommendation") if isinstance(inspection, dict) else None)
                    if isinstance(image_rec, dict) and image_rec.get("image") and image_rec.get("image") not in detected_imgs:
                        detected_imgs.append(str(image_rec.get("image")).strip())
                    if img and img not in detected_imgs:
                        detected_imgs.append(img)

                    primary_img = detected_imgs[0] if detected_imgs else ""
                    required_inputs = setup_handoff.required_setup_inputs(setup_source_result)
                    has_compose = bool((inspection.get("compose_info") or {}).get("services")) if isinstance(inspection, dict) else False

                    options_list = []
                    # 1. Compose stack option if compose services exist
                    if has_compose:
                        rec_label = " (Recommended)" if not primary_img else ""
                        options_list.append(f"[OPTION:Docker Compose Stack{rec_label}|deployment_method:compose_stack]")

                    # 2. Ready Docker image option if detected or provided
                    if primary_img:
                        rec_label = " (Recommended)" if not has_compose else ""
                        options_list.append(f"[OPTION:Run Docker Image{rec_label}: {primary_img}|deployment_method:registry_image:{primary_img}]")

                    # 3. Build from Git Source (Railpack)
                    if repo or stype == "git" or not (has_compose or primary_img):
                        rec_label = " (Recommended)" if not (has_compose or primary_img) else ""
                        options_list.append(f"[OPTION:Build from Git Source (Railpack){rec_label}|deployment_method:git_build]")

                    if not has_compose and isinstance(inspection, dict):
                        from services.apps_engine import database_provider_capabilities
                        detected_kinds = {
                            str(item.get("kind") or "").lower()
                            for item in (inspection.get("database_detections") or [])
                            if isinstance(item, dict)
                        }
                        for record in database_provider_capabilities.provider_capabilities(force=True):
                            kind = str(record.get("kind") or "")
                            if kind not in detected_kinds:
                                continue
                            for choice in record.get("providers") or []:
                                provider = str(choice.get("provider_id") or choice.get("id") or "")
                                state = str(choice.get("managed_dependency_state") or choice.get("state") or "")
                                if provider == "docker":
                                    options_list.append(f"[OPTION:Private {kind} container (Recommended)|provider.{kind}:docker]")
                                elif state == "active":
                                    options_list.append(f"[OPTION:{choice.get('label')}|provider.{kind}:{provider}]")
                                elif state == "stopped" and choice.get("can_activate"):
                                    options_list.append(f"[OPTION:Activate {choice.get('label')} from Dependencies|provider.{kind}:activate:{provider}]")

                    for item in required_inputs:
                        req_flag = "required" if item.get("required") else "optional"
                        options_list.append(
                            f"[INPUT:{item['name']}|{item['placeholder']}|{item['label']}|{req_flag}]"
                        )
                    options_str = "\n".join(options_list)
                    setup_interview_options_str = options_str

                    action_instruction = (
                        "Present the source inspection facts clearly in clean Markdown tables (Application Overview, Services, Detected Databases, Configuration). "
                        "Do NOT call proposal planning tools yet. "
                        "Declare every unresolved deployment choice, provider choice, and documented non-secret input together using the exact interactive tags below. "
                        "Do not ask for passwords, keys, tokens, or secrets. The browser will show one question at a time and send one combined answer only after completion. "
                        f"Provide these interactive option and input tags exactly:\n{options_str}\n"
                        "Wait for the combined interview answer before generating the reviewed plan."
                    )
                elif needs_stack:
                    action_instruction = (
                        "Compose services or auxiliary datastores were detected. You MUST call `propose_stack_install` to create a restricted stack setup plan."
                    )
                else:
                    action_instruction = (
                        "Propose the installation plan using `propose_app_install` (or `propose_stack_install` if multi-container)."
                    )
                messages.append({
                    "role": "user",
                    "content": (
                        f"[Pre-collected Source Inspection Facts for {repo or img}]:\n"
                        f"{json.dumps(setup_source_result)}\n\n"
                        f"Target Domain: {setup_target_domain or 'not specified'}\n"
                        f"Using the pre-inspected facts above, {action_instruction}"
                    ),
                })
        except Exception as exc:
            yield _activity_event("inspect_app_source", "error", {"repository_url": repo, "image_reference": img})

    # 2. Fast 1-Turn Pre-Diagnostics for App Engine Diagnostic Tasks:
    if tools_enabled and is_app_diag and app_id:
        yield _activity_event("get_app_engine_diagnostics", "start", {"app_id": app_id})
        try:
            diag_res = await tools.execute_tool(
                db=db,
                tool_name="get_app_engine_diagnostics",
                arguments={"app_id": app_id},
                session_id=session_id,
                user_id=user_id,
                secrets_allowed=secrets_allowed,
            )
            yield _activity_event("get_app_engine_diagnostics", "done", {"app_id": app_id})
            if diag_res.get("status") == "ok":
                tool_was_executed = True
                diag_data = diag_res.get("diagnostics") or diag_res
                messages.append({
                    "role": "user",
                    "content": (
                        f"[Pre-collected App Engine Runtime Diagnostics for App #{app_id}]:\n"
                        f"{json.dumps(diag_data)}\n\n"
                        "Using the pre-collected container runtime logs, deployment logs, process status, and service list above:\n"
                        "1. Diagnose the exact root cause from the error and container stderr.\n"
                        "2. Inspect the `services` dictionary to confirm which containers/datastores are actually provisioned in the stack. Never configure connection URLs for services or auxiliary containers that do not exist in the stack; if an unprovisioned optional service variable causes a crash due to empty or missing URL, unset it (e.g. with '__unset__') so the application runs with its active services.\n"
                        "3. Explicitly list the files and variables being edited, explain container recreation lifecycle, and stage the fix using `propose_container_app_patch`."
                    ),
                })
        except Exception as exc:
            yield _activity_event("get_app_engine_diagnostics", "error", {"app_id": app_id})

    if tools_enabled:
        # Determine scoped tool definitions
        if setup_plan_required:
            if setup_handoff.is_recommendation_decision_pending(setup_source_result, user_message):
                tool_names_to_load = frozenset()
            elif setup_source_result and setup_source_result.get("status") == "ok":
                from services.official_stacks.stack_synthesizer import requires_multi_container_stack
                if requires_multi_container_stack(setup_source_result):
                    tool_names_to_load = frozenset({"propose_stack_install"})
                else:
                    tool_names_to_load = frozenset({"propose_app_install", "propose_stack_install"})
                if setup_handoff.needs_documentation_fallback(setup_source_result):
                    tool_names_to_load |= frozenset({"fetch_web_documentation"})
            else:
                tool_names_to_load = setup_handoff.SETUP_TOOL_NAMES
        elif is_app_diag and app_id:
            tool_names_to_load = setup_handoff.APP_DIAGNOSTIC_TOOL_NAMES
        else:
            tool_names_to_load = None

        tool_defs = tools.get_tool_definitions(
            active.provider_type,
            tool_names=tool_names_to_load,
        )
        tool_counts: Dict[str, int] = {}
        if setup_source_result and setup_source_result.get("status") == "ok":
            tool_counts["inspect_app_source"] = 1
        max_tool_iterations = 4 if setup_plan_required else (4 if (is_app_diag and app_id) else 6)


        async def _execute_tool(fn_name: str, fn_args: Dict[str, Any]) -> Dict[str, Any]:
            """Execute approved tools while bounding setup-only evidence collection."""
            nonlocal sensitive_file_blocked, setup_stack_correction_allowed, setup_stack_correction_reason, setup_source_result, setup_documentation_failed, setup_provider_choice
            limited = setup_handoff.tool_limit_result(
                task_type,
                fn_name,
                tool_counts,
                allow_stack_correction=setup_stack_correction_allowed,
            )
            if limited is not None:
                return limited
            if fn_name == "fetch_web_documentation":
                if not setup_source_result or setup_source_result.get("status") != "ok":
                    return {
                        "status": "local_inspection_required",
                        "message": "Inspect the application source before using the documentation fallback.",
                    }
                if not setup_handoff.needs_documentation_fallback(setup_source_result):
                    return {
                        "status": "local_evidence_sufficient",
                        "message": "Use the collected local documentation evidence; no external read is needed.",
                    }
            tool_counts[fn_name] = tool_counts.get(fn_name, 0) + 1
            if fn_name == "fetch_web_documentation" and not setup_handoff.setup_documentation_url_allowed(
                setup_source_result, user_message, str(fn_args.get("url") or ""),
            ):
                setup_documentation_failed = True
                return {
                    "status": "documentation_source_not_verified",
                    "message": "Use an inspected official source URL or an HTTPS documentation URL supplied by the user.",
                }
            if fn_name in {"propose_app_install", "propose_stack_install", "propose_official_stack_install"}:
                if setup_target_domain and not fn_args.get("domain_name"):
                    fn_args["domain_name"] = setup_target_domain
                if repo and not fn_args.get("repository_url") and fn_args.get("source_type") != "image":
                    fn_args["repository_url"] = repo
                if img and not fn_args.get("image_reference"):
                    fn_args["image_reference"] = img
            tool_output = await tools.execute_tool(
                db=db,
                tool_name=fn_name,
                arguments=fn_args,
                session_id=session_id,
                user_id=user_id,
                secrets_allowed=secrets_allowed,
            )
            if fn_name == "fetch_web_documentation" and tool_output.get("status") != "ok":
                setup_documentation_failed = True
            if fn_name == "read_website_file" and tool_output.get("status") == "secrets_blocked":
                sensitive_file_blocked = True
            if fn_name == "inspect_app_source" and tool_output.get("status") == "ok":
                setup_source_result = tool_output
            if fn_name in {"propose_app_install", "propose_stack_install", "propose_official_stack_install"}:
                if tool_output.get("status") == "provider_choice_required":
                    setup_provider_choice = tool_output
                if tool_output.get("status") != "ok":
                    message = str(tool_output.get("message") or "The planning tool rejected this proposal.").strip()
                    if message:
                        setup_plan_errors.append(message)
                if setup_handoff.needs_stack_correction(fn_name, tool_output):
                    setup_stack_correction_allowed = True
                    setup_stack_correction_reason = str(tool_output.get("message") or "").strip()
            return tool_output

        reasoning_active = False
        for iteration in range(max_tool_iterations):
            try:
                if setup_plan_required and not reasoning_active:
                    yield _activity_event("ai_reasoning", "start", {"domain": setup_target_domain or repo})
                    reasoning_active = True
                norm_messages = _normalize_messages_for_llm(messages, active.provider_type)
                tool_step = await engine.chat_completion_step(
                    provider_type=active.provider_type,
                    base_url=active.base_url,
                    api_key=api_key,
                    model_name=effective_model,
                    messages=norm_messages,
                    tools=tool_defs,
                    temperature=active.temperature,
                    max_tokens=active.max_tokens,
                )
                if reasoning_active:
                    yield _activity_event("ai_reasoning", "done", {"domain": setup_target_domain or repo})
                    reasoning_active = False
                tool_calls = tool_step.get("tool_calls") or []
                step_content = tool_step.get("content") or ""

                if not tool_calls:
                    if setup_provider_choice:
                        break
                    # Check for DeepSeek DSML or XML pseudo tool call syntax in text
                    tool_calls = _extract_text_tool_calls(step_content)
                    is_text_pseudo_tool = bool(tool_calls)
                else:
                    is_text_pseudo_tool = False

                if not tool_calls:
                    if setup_handoff.is_recommendation_decision_pending(setup_source_result, user_message):
                        break
                    if (
                        setup_plan_required
                        and setup_source_result
                        and not setup_server_plan_attempted
                        and not setup_plan_id
                    ):
                        fallback_args = app_setup_tools.stack_plan_args_from_inspection(
                            setup_source_result,
                            domain_name=setup_target_domain,
                        )
                        setup_server_plan_attempted = True
                        if fallback_args:
                            yield _activity_event("propose_stack_install", "start", fallback_args)
                            try:
                                tool_output = await _execute_tool("propose_stack_install", fallback_args)
                                setup_plan_id = setup_plan_id or _setup_plan_id("propose_stack_install", tool_output)
                                yield _activity_event("propose_stack_install", "done", fallback_args)
                            except Exception as exc:
                                tool_output = {"status": "error", "message": str(exc)}
                                yield _activity_event("propose_stack_install", "error", fallback_args)
                            messages.append({
                                "role": "user",
                                "content": f"[Server setup fallback result]:\n{json.dumps(tool_output)}",
                            })
                            if setup_plan_id:
                                break
                            continue
                    if (
                        setup_plan_required
                        and setup_stack_correction_allowed
                        and not setup_stack_correction_prompted
                        and not setup_plan_id
                    ):
                        correction_message = setup_handoff.STACK_CORRECTION_MESSAGE
                        if setup_stack_correction_reason:
                            correction_message = (
                                f"{correction_message}\n\n"
                                f"Previous single-app rejection: {setup_stack_correction_reason[:420]}"
                            )
                        messages.append({"role": "user", "content": correction_message})
                        setup_stack_correction_prompted = True
                        setup_plan_tool_prompted = True
                        continue
                    if setup_plan_required and not setup_plan_tool_prompted and not setup_plan_id:
                        messages.append({
                            "role": "user",
                            "content": setup_handoff.PLAN_TOOL_REQUIRED_MESSAGE,
                        })
                        setup_plan_tool_prompted = True
                        continue
                    break

                tool_was_executed = True
                raw_msg = tool_step.get("raw_message")

                if is_text_pseudo_tool:
                    # Text-based pseudo tool calls (DSML/XML)
                    messages.append({"role": "assistant", "content": step_content})
                    tool_results_list = []
                    for tc in tool_calls:
                        fn_name = tc.get("name")
                        fn_args = tc.get("arguments") or {}
                        yield _activity_event(fn_name, "start", fn_args)
                        try:
                            tool_output = await _execute_tool(fn_name, fn_args)
                            p_id = _setup_plan_id(fn_name, tool_output)
                            if p_id and not setup_plan_id:
                                setup_plan_id = p_id
                                setup_plan_kind = "patch" if fn_name == "propose_container_app_patch" else "install"
                            yield _activity_event(fn_name, "done", fn_args)
                        except Exception as e:
                            tool_output = {"status": "error", "message": str(e)}
                            yield _activity_event(fn_name, "error", fn_args)
                        tool_results_list.append(f"[Result for {fn_name}]:\n{json.dumps(tool_output)}")

                    messages.append({
                        "role": "user",
                        "content": "\n\n".join(tool_results_list) + "\n\nPresent the findings directly and clearly in clean Markdown now without repeating or outputting tool calls.",
                    })

                elif active.provider_type == "anthropic":
                    # Anthropic Claude native tool calling
                    if raw_msg:
                        messages.append(raw_msg)
                    else:
                        messages.append({"role": "assistant", "content": step_content})

                    tool_results = []
                    for tc in tool_calls:
                        fn_name = tc.get("name")
                        fn_args = tc.get("arguments") or {}
                        tc_id = tc.get("id") or f"tool_{len(messages)}"
                        yield _activity_event(fn_name, "start", fn_args)
                        try:
                            tool_output = await _execute_tool(fn_name, fn_args)
                            p_id = _setup_plan_id(fn_name, tool_output)
                            if p_id and not setup_plan_id:
                                setup_plan_id = p_id
                                setup_plan_kind = "patch" if fn_name == "propose_container_app_patch" else "install"
                            yield _activity_event(fn_name, "done", fn_args)
                        except Exception as e:
                            tool_output = {"status": "error", "message": str(e)}
                            yield _activity_event(fn_name, "error", fn_args)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tc_id,
                            "content": json.dumps(tool_output),
                        })

                    messages.append({
                        "role": "user",
                        "content": tool_results,
                    })

                else:
                    # Native OpenAI-compatible tool calling
                    if raw_msg and raw_msg.get("tool_calls"):
                        messages.append(raw_msg)
                    else:
                        norm_calls = [
                            {
                                "id": tc.get("id") or f"call_{idx}_{tc.get('name')}",
                                "type": "function",
                                "function": {
                                    "name": tc.get("name"),
                                    "arguments": json.dumps(tc.get("arguments") or {}) if isinstance(tc.get("arguments"), dict) else str(tc.get("arguments") or "{}"),
                                },
                            }
                            for idx, tc in enumerate(tool_calls)
                        ]
                        messages.append({
                            "role": "assistant",
                            "content": step_content if step_content else None,
                            "tool_calls": norm_calls,
                        })

                    for tc in tool_calls:
                        fn_name = tc.get("name")
                        fn_args = tc.get("arguments") or {}
                        tc_id = tc.get("id") or f"call_{len(messages)}"

                        yield _activity_event(fn_name, "start", fn_args)
                        try:
                            tool_output = await _execute_tool(fn_name, fn_args)
                            p_id = _setup_plan_id(fn_name, tool_output)
                            if p_id and not setup_plan_id:
                                setup_plan_id = p_id
                                setup_plan_kind = "patch" if fn_name == "propose_container_app_patch" else "install"
                            yield _activity_event(fn_name, "done", fn_args)
                        except Exception as exc:
                            tool_output = {"status": "error", "message": str(exc)}
                            yield _activity_event(fn_name, "error", fn_args)

                        tool_json_str = json.dumps(tool_output)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "name": fn_name,
                            "content": tool_json_str,
                        })
            except Exception as exc:
                logger.warning("Tool calling step failed or skipped: %s. Injecting error context and continuing stream.", exc)
                messages.append({
                    "role": "user",
                    "content": f"[Tool step failed: {str(exc)}. Report this failure clearly to the user and present any results already obtained above.]",
                })
                break

        # After tool loop: ensure a setup plan is created if this was a setup request and decision is not pending
        if (
            setup_plan_required
            and not setup_plan_id
            and not setup_documentation_failed
            and not setup_provider_choice
            and not setup_handoff.is_recommendation_decision_pending(setup_source_result, user_message)
        ):
            stype, repo, img = _extract_setup_source(user_message, context_text)
            try:
                fallback_plan = await setup_plan_builder.build_automatic_setup_plan(
                    db=db,
                    session_id=session_id,
                    user_id=user_id,
                    source_type=stype,
                    repository_url=repo,
                    image_reference=img,
                    domain_name=setup_target_domain,
                    inspection_result=setup_source_result,
                )
                setup_plan_id = fallback_plan.plan_id
            except Exception as exc:
                logger.warning("Automatic setup plan creation fallback failed: %s", exc)

        if tool_was_executed:
            if setup_plan_required:
                if setup_documentation_failed:
                    content_str = (
                        "The single official documentation read failed. Continue from the existing local inspection facts, "
                        "state that documentation was unavailable, and ask one concise [INPUT:] or [OPTION:] question "
                        "for the meaningful unknown needed to finish the reviewed plan. Do not stop setup or claim a plan is ready."
                    )
                elif setup_provider_choice:
                    dependency_id = str(setup_provider_choice.get("dependency_id") or "")
                    provider_state = str(setup_provider_choice.get("provider_state") or "unavailable")
                    if dependency_id and provider_state == "stopped":
                        content_str = (
                            "The selected managed provider is stopped. No plan was created. "
                            f"Activate it explicitly, then send the combined interview answer again: [ACTION:OPEN_DEPENDENCY:{dependency_id}]"
                        )
                    else:
                        content_str = (
                            "The selected managed provider is unavailable, so no plan was created. "
                            "Choose the documented private container provider or install/repair the dependency from Dependencies."
                        )
                elif setup_handoff.is_setup_interview_pending(setup_source_result, user_message):
                    tags_block = f"\n{setup_interview_options_str}\n" if setup_interview_options_str else ""
                    content_str = (
                        "Now summarize the inspected application architecture, services, and deployment configuration in clean, structured Markdown tables. "
                        "Do NOT output tool calls, JSON ASTs, or internal schema errors. "
                        "Declare every unresolved deployment choice, provider choice, and documented non-secret input together using the exact interactive tags below:"
                        f"{tags_block}"
                        "Do not ask for passwords, keys, tokens, or secrets. "
                        "State clearly that the required setup details are requested from the user to finalize the deployment plan. "
                        "Do NOT claim the reviewed setup plan is ready to deploy yet."
                    )
                else:
                    content_str = (
                        "The reviewed setup plan has been created and validated. "
                        "Briefly summarize the chosen configuration (e.g. selected deployment option, configured target domain, and provided credentials/settings) in a clean Markdown summary. "
                        "Do NOT ask the setup interview questions again, do NOT list options or request admin email again, and do NOT output [OPTION:] or [INPUT:] tags. "
                        "State clearly that the reviewed setup plan is ready to deploy using the action button below."
                    )
                messages.append({
                    "role": "user",
                    "content": content_str,
                })
            else:
                messages.append({
                    "role": "user",
                    "content": (
                        "Now present ALL findings above in clean, structured Markdown directly to the user. "
                        "Do NOT output any internal reasoning or planning text. Do NOT make further tool calls. "
                        "Use the required output formats: ```security for security findings, "
                        "```log for log output, markdown tables for records, "
                        "and 📁/📄 bullet lists for file listings."
                    ),
                })

    # Stream final assistant response
    if setup_plan_required and setup_plan_id:
        yield _activity_event("ai_planning", "start", {"domain": setup_target_domain or repo})
        yield _activity_event("ai_planning", "done", {"domain": setup_target_domain or repo})

    full_response = []
    has_error = False
    visible_filter = visible_output.VisibleOutputFilter()
    try:
        flattened_messages = _flatten_tool_messages_for_text_generation(messages)
        final_normalized_messages = _normalize_messages_for_llm(flattened_messages, active.provider_type)
        async for chunk in engine.stream_chat(
            provider_type=active.provider_type,
            base_url=active.base_url,
            api_key=api_key,
            model_name=effective_model,
            messages=final_normalized_messages,
            temperature=active.temperature,
            max_tokens=active.max_tokens,
        ):
            visible_chunk = visible_filter.push(chunk)
            if visible_chunk:
                full_response.append(visible_chunk)
                yield visible_chunk
        final_chunk = visible_filter.finish()
        if final_chunk:
            full_response.append(final_chunk)
            yield final_chunk
    except engine.AIProviderError as exc:
        has_error = True
        err_msg = f"\n\n[Error from AI Provider: {exc.message}]"
        full_response.append(err_msg)
        yield err_msg
    except Exception as exc:
        has_error = True
        err_msg = f"\n\n[Error: {str(exc)}]"
        full_response.append(err_msg)
        yield err_msg

    # Diagnostic auto-patch fallback: guarantee reviewed redeploy button on failed/diagnosed apps
    if app_id and not setup_plan_id and is_app_diag and not has_error:
        try:
            from models.container_app import ContainerApp
            from plugins.ai_helper.tools import app_setup as _app_setup_tools
            app_obj = await db.get(ContainerApp, app_id)
            if app_obj is not None:
                patch_args: Dict[str, Any] = {}
                combined_diag_text = (
                    user_message + "\n" + (context_text or "") + "\n" +
                    "".join(full_response)
                )
                fix_img_match = re.search(
                    r"(?:correct|suggested|recommend(?:ed)?)\s+image\s*(?:reference)?(?:\s*(?:is|should be|:))\s*([a-z0-9_.-]+/[a-z0-9_.-]+(?::[a-z0-9_.-]+)?)",
                    combined_diag_text,
                    re.IGNORECASE,
                )
                if fix_img_match:
                    cand_img = fix_img_match.group(1).strip()
                    if cand_img != app_obj.image_reference:
                        patch_args["image_reference"] = cand_img

                auto_patch_res = await _app_setup_tools.propose_container_app_patch(
                    db=db,
                    app_id=app_id,
                    patch=patch_args,
                    evidence=["Diagnostic analysis generated reviewed deployment draft."],
                    summary=f"Redeploy & apply fix for App Engine app #{app_id}",
                    confidence=0.95,
                    session_id=session_id,
                    user_id=user_id,
                )
                if auto_patch_res.get("status") == "ok":
                    setup_plan_id = auto_patch_res.get("plan_id")
                    setup_plan_kind = "patch"
        except Exception as exc:
            logger.warning("Final diagnostic auto-patch fallback failed: %s", exc)

    if setup_plan_required and not setup_plan_id and setup_interview_options_str and not has_error:
        full_text_so_far = "".join(full_response)
        if "[OPTION:" not in full_text_so_far and "[INPUT:" not in full_text_so_far:
            append_tags = f"\n\n{setup_interview_options_str}"
            full_response.append(append_tags)
            yield append_tags

    if setup_plan_id:
        kind_suffix = ":patch" if setup_plan_kind == "patch" else ""

        setup_action = f"\n\n[ACTION:APP_SETUP_PLAN:{setup_plan_id}{kind_suffix}]"
        full_response.append(setup_action)
        yield setup_action

    if sensitive_file_blocked and not secrets_allowed and not has_error:
        unlock_action = "\n\nSensitive file access is blocked. [ACTION:UNLOCK_SENSITIVE_FILE:session]"
        full_response.append(unlock_action)
        yield unlock_action

    # Save assistant response to database if not a provider error
    complete_text = "".join(full_response).strip()
    if complete_text and not has_error:
        persisted_text = _sanitize_history_content(
            complete_text,
            allow_setup_action=bool(setup_plan_id),
            allow_sensitive_file_unlock=sensitive_file_blocked and not secrets_allowed,
        ) or complete_text
        assistant_record = AiChatMessage(
            session_id=session_id,
            role="assistant",
            content=persisted_text,
            context_key=context_key,
        )
        db.add(assistant_record)
        session_record.message_count += 1
        session_record.updated_at = datetime.now(tz=timezone.utc)
        await db.commit()
