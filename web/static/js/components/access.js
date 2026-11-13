// ==========================================================================
// Access gate: with MININOC_AUTH_READ=1 the page asks for a sign-in before it shows or requests anything, and the
// very first visit creates the administrator. The server decides (GET /api/auth-status); this file only asks.
// ==========================================================================

var ACCESS_MIN_PASSWORD = 8;

function gateEl(id) {
  return document.getElementById(id);
}

function setGateMessage(text, isError) {
  var msg = gateEl("gate-msg");
  if (!msg) {
    return;
  }
  msg.textContent = text;
  msg.className = isError ? "gate-msg error" : "gate-msg";
}

// Shows the card in "create the first admin" or "sign in" mode.
function showAccessGate(status) {
  var creating = !!status.needs_admin;
  gateEl("gate-title").textContent = creating ? "Welcome to PingPoint NOC" : "Sign in to PingPoint NOC";
  gateEl("gate-intro").textContent = creating
    ? "Create the administrator account. It is the only way in until you add more users."
    : "This NOC asks for a sign-in before it shows anything.";
  gateEl("gate-confirm-wrap").classList.toggle("hidden", !creating);
  gateEl("gate-setup-wrap").classList.toggle("hidden", !(creating && status.setup_token_required));
  gateEl("gate-submit").textContent = creating ? "Create administrator" : "Sign in";
  gateEl("gate-password").setAttribute("autocomplete", creating ? "new-password" : "current-password");
  if (creating && !gateEl("gate-username").value) {
    gateEl("gate-username").value = "admin";
  }
  setGateMessage("", false);
  gateEl("access-gate").classList.remove("hidden");
  document.body.classList.add("gated");
  gateEl(creating ? "gate-password" : "gate-username").focus();
}

function hideAccessGate() {
  gateEl("access-gate").classList.add("hidden");
  document.body.classList.remove("gated");
}

// Resolves once the visitor is allowed in (or the gate is not needed). Never rejects: if the server cannot be asked,
// the page boots as before and shows its own errors.
async function runAccessGate() {
  var status = null;
  try {
    var resp = await fetch("/api/auth-status");
    if (!resp.ok) {
      return;
    }
    status = await resp.json();
  } catch (e) {
    return;
  }
  if (!status.read_auth) {
    return;
  }
  STATE.readAuth = true;
  authSession = readStoredSession();
  try {
    var me = await fetch("/api/me", { headers: authHeaders({}) });
    if (me.ok) {
      return;
    }
  } catch (e) {
    // fall through to the sign-in
  }
  storeSession(null);
  await new Promise(function (resolve) {
    showAccessGate(status);
    gateEl("gate-form").addEventListener("submit", async function (ev) {
      ev.preventDefault();
      var username = gateEl("gate-username").value.trim();
      var password = gateEl("gate-password").value;
      var creating = !!status.needs_admin;
      if (creating) {
        if (password.length < ACCESS_MIN_PASSWORD) {
          setGateMessage("The password must be at least " + ACCESS_MIN_PASSWORD + " characters.", true);
          return;
        }
        if (password !== gateEl("gate-confirm").value) {
          setGateMessage("The two passwords are not the same.", true);
          return;
        }
      }
      var headers = { "Content-Type": "application/json" };
      var setupToken = gateEl("gate-setup").value.trim();
      if (creating && setupToken) {
        headers["X-Admin-Token"] = setupToken;
      }
      setGateMessage(creating ? "Creating the account..." : "Signing in...", false);
      try {
        var response = await fetch(creating ? "/api/bootstrap-admin" : "/api/login", {
          method: "POST",
          headers: headers,
          body: JSON.stringify({ username: username, password: password })
        });
        var payload = {};
        try {
          payload = await response.json();
        } catch (e) {
          payload = {};
        }
        if (!response.ok) {
          throw new Error(payload.detail || ("HTTP " + response.status));
        }
        gateEl("gate-password").value = "";
        gateEl("gate-confirm").value = "";
        gateEl("gate-setup").value = "";
        storeSession(payload);
        hideAccessGate();
        resolve();
      } catch (err) {
        gateEl("gate-password").value = "";
        setGateMessage(err.message, true);
      }
    });
  });
}

// A request that comes back 401 means the session ended: start over from the gate (the #hash link is kept).
function sessionEnded() {
  storeSession(null);
  window.location.reload();
}
