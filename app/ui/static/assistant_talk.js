(function () {
  const form = document.getElementById("assistant-talk-form");
  const statusEl = document.getElementById("talk-live-status");
  const outputEl = document.getElementById("talk-live-output");
  const metaEl = document.getElementById("talk-live-meta");
  if (!form || !statusEl || !outputEl) {
    return;
  }

  const STORAGE_KEY = "deck_assistant_turn_id";

  function setStatus(text) {
    statusEl.textContent = text;
    statusEl.hidden = !text;
  }

  function renderMeta(payload) {
    if (!metaEl || !payload) {
      return;
    }
    const parts = [];
    if (payload.persisted !== undefined) {
      parts.push(payload.persisted ? "Gespeichert, vom Owner gewählt" : "Nicht gespeichert");
    }
    if (payload.evidence_status) {
      parts.push(payload.evidence_status);
    }
    if (payload.data_class) {
      parts.push(payload.data_class);
    }
    metaEl.textContent = parts.join(" · ");
    metaEl.hidden = parts.length === 0;
  }

  function appendText(text) {
    outputEl.hidden = false;
    outputEl.textContent += text;
  }

  function beginTurn() {
    outputEl.textContent = "";
    outputEl.hidden = true;
    if (metaEl) {
      metaEl.hidden = true;
      metaEl.textContent = "";
    }
    setStatus("Modell antwortet …");
  }

  async function pollStatus(turnId) {
    const response = await fetch("/ui/assistant/talk/" + encodeURIComponent(turnId), {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error("status");
    }
    return response.json();
  }

  async function resumeTurn(turnId) {
    beginTurn();
    try {
      const snapshot = await pollStatus(turnId);
      if (snapshot.partial) {
        appendText(snapshot.partial);
      }
      if (snapshot.state === "done" && snapshot.answer) {
        outputEl.textContent = snapshot.answer;
        outputEl.hidden = false;
        renderMeta(snapshot);
        setStatus("");
        sessionStorage.removeItem(STORAGE_KEY);
        return;
      }
      if (snapshot.state === "error") {
        setStatus("Fehler: " + (snapshot.error || "assistant_model_unavailable"));
        sessionStorage.removeItem(STORAGE_KEY);
        return;
      }
      subscribeStream(turnId);
    } catch (_err) {
      sessionStorage.removeItem(STORAGE_KEY);
    }
  }

  function subscribeStream(turnId) {
    sessionStorage.setItem(STORAGE_KEY, turnId);
    if (typeof EventSource !== "undefined") {
      const source = new EventSource(
        "/ui/assistant/talk/" + encodeURIComponent(turnId) + "/stream"
      );
      source.addEventListener("status", function (event) {
        try {
          const data = JSON.parse(event.data);
          if (data.state === "queued") {
            setStatus("Warte auf Modell …");
          } else if (data.state === "running") {
            setStatus("Modell antwortet …");
          }
        } catch (_err) {
          /* ignore malformed frames */
        }
      });
      source.addEventListener("chunk", function (event) {
        try {
          const data = JSON.parse(event.data);
          if (data.text) {
            appendText(data.text);
          }
        } catch (_err) {
          /* ignore malformed frames */
        }
      });
      source.addEventListener("done", function (event) {
        try {
          const data = JSON.parse(event.data);
          if (data.answer) {
            outputEl.textContent = data.answer;
            outputEl.hidden = false;
          }
          renderMeta(data);
          setStatus("");
          sessionStorage.removeItem(STORAGE_KEY);
        } catch (_err) {
          setStatus("Antwort unvollständig.");
        }
        source.close();
      });
      source.addEventListener("failed", function (event) {
        try {
          const data = JSON.parse(event.data);
          setStatus("Fehler: " + (data.error || "assistant_model_unavailable"));
        } catch (_err) {
          setStatus("Verbindung unterbrochen.");
        }
        source.close();
        sessionStorage.removeItem(STORAGE_KEY);
      });
      source.onerror = function () {
        source.close();
        fallbackPoll(turnId);
      };
      return;
    }
    fallbackPoll(turnId);
  }

  async function fallbackPoll(turnId) {
  sessionStorage.setItem(STORAGE_KEY, turnId);
    const deadline = Date.now() + 85000;
    while (Date.now() < deadline) {
      try {
        const snapshot = await pollStatus(turnId);
        if (snapshot.partial && outputEl.textContent.length < snapshot.partial.length) {
          outputEl.textContent = snapshot.partial;
          outputEl.hidden = false;
        }
        if (snapshot.state === "done") {
          outputEl.textContent = snapshot.answer || outputEl.textContent;
          outputEl.hidden = false;
          renderMeta(snapshot);
          setStatus("");
          sessionStorage.removeItem(STORAGE_KEY);
          return;
        }
        if (snapshot.state === "error") {
          setStatus("Fehler: " + (snapshot.error || "assistant_model_unavailable"));
          sessionStorage.removeItem(STORAGE_KEY);
          return;
        }
      } catch (_err) {
        setStatus("Verbindung unterbrochen.");
        return;
      }
      await new Promise(function (resolve) {
        setTimeout(resolve, 900);
      });
    }
    setStatus("Zeitlimit erreicht.");
    sessionStorage.removeItem(STORAGE_KEY);
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    const body = new FormData(form);
    body.set("stream", "yes");
    const button = form.querySelector("button[type=submit], button:not([type])");
    if (button) {
      button.disabled = true;
    }
    beginTurn();
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: body,
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        setStatus("Anfrage abgewiesen.");
        return;
      }
      const payload = await response.json();
      if (!payload.turn_id) {
        setStatus("Ungültige Antwort.");
        return;
      }
      subscribeStream(payload.turn_id);
    } catch (_err) {
      setStatus("Netzwerkfehler.");
    } finally {
      if (button) {
        button.disabled = false;
      }
    }
  });

  const existing = sessionStorage.getItem(STORAGE_KEY);
  if (existing) {
    resumeTurn(existing);
  }
})();
