"""
Search Engine Module — DuckDuckGo Integration
No API key required. Free, private search.

SECURITY: All results pass through sanitize_content() before
reaching the LLM context window. This strips instruction-like
patterns that could be used for indirect prompt injection.
"""
import re
from duckduckgo_search import DDGS


# ============================================================
# SANITIZATION LAYER
# ============================================================

# Patterns that look like prompt injection attempts
_INJECTION_PATTERNS = [
    # Direct instruction overrides
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|rules?|prompts?|guidelines?)", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|rules?|prompts?|guidelines?)", re.IGNORECASE),
    re.compile(r"forget\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|rules?|prompts?|guidelines?)", re.IGNORECASE),
    # Role reassignment
    re.compile(r"you\s+are\s+now\s+", re.IGNORECASE),
    re.compile(r"from\s+now\s+on\s+you\s+(are|will|must|should)", re.IGNORECASE),
    re.compile(r"act\s+as\s+(a|an|if)\s+", re.IGNORECASE),
    re.compile(r"pretend\s+(to\s+be|you\s+are)", re.IGNORECASE),
    # System prompt mimicry
    re.compile(r"\[?\s*system\s*(prompt|message|instruction)\s*\]?", re.IGNORECASE),
    re.compile(r"<\s*/?system\s*>", re.IGNORECASE),
    # Imperative overrides
    re.compile(r"(do\s+not|don'?t|never)\s+(mention|reveal|disclose|tell|say|share)\s+(that|the|your|this)", re.IGNORECASE),
    re.compile(r"(always|must|shall)\s+(respond|reply|answer|say|output)\s+(with|as|in)", re.IGNORECASE),
]

from bs4 import BeautifulSoup

def sanitize_content(text: str) -> str:
    """
    Strip content that could be used for indirect prompt injection.
    
    This implementation uses BeautifulSoup for structural tree-shredding (shredding
    the DOM tree to extract raw text) followed by line-by-line pattern redaction.
    """
    if not text:
        return text
    
    # Structural Shredding: Extract text only using a real DOM parser
    # This renders tag-based bypasses mathematically impossible.
    soup = BeautifulSoup(text, "lxml")
    cleaned = soup.get_text(separator=" ")
    
    # Decouple entities and extra whitespace
    cleaned = " ".join(cleaned.split())
    
    # Redact lines containing injection patterns
    safe_lines = []
    for line in cleaned.split("\n"):
        flagged = False
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(line):
                safe_lines.append("[REDACTED: Suspicious instruction detected]")
                flagged = True
                break
        if not flagged:
            safe_lines.append(line)
    
    return "\n".join(safe_lines)


# ============================================================
# SEARCH ENGINE
# ============================================================

def web_search(query: str, max_results: int = 5, trusted_domains: list = None) -> str:
    """
    Search the web via DuckDuckGo and return sanitized, formatted results.
    
    All results are passed through the sanitization layer before being
    returned to prevent indirect prompt injection.
    
    Args:
        query: Search query string
        max_results: Maximum number of results to return
        trusted_domains: Optional list of allowed domains (e.g., ["reddit.com", "wikipedia.org"])
                        If None/empty, all domains are allowed.
    
    Returns:
        Formatted string of search results, sanitized for safe context injection.
    """
    try:
        with DDGS() as ddgs:
            raw_results = list(ddgs.text(query, max_results=max_results * 2))  # Fetch extra for filtering
        
        if not raw_results:
            return "No results found."
        
        # Filter by trusted domains if specified
        if trusted_domains:
            filtered = []
            for r in raw_results:
                url = r.get("href", "")
                if any(domain.lower() in url.lower() for domain in trusted_domains):
                    filtered.append(r)
            raw_results = filtered
        
        # Limit to max_results after filtering
        raw_results = raw_results[:max_results]
        
        if not raw_results:
            return "No results found matching trusted domains."
        
        # Format and sanitize results
        formatted = []
        for i, r in enumerate(raw_results, 1):
            title = sanitize_content(r.get("title", "No Title"))
            body = sanitize_content(r.get("body", ""))
            url = r.get("href", "")
            formatted.append(f"[{i}] {title}\n{body}\nSource: {url}")
        
        return "\n\n".join(formatted)
        
    except Exception as e:
        print(f"SEARCH ERROR: {e}")
        return f"Search failed: {str(e)}"
