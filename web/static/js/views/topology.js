// ==========================================================================
// Network & Infrastructure Topology View (tab-topology)
// Zero-CDN, Pure SVG Interactive Graph & Dependency Tree
// ==========================================================================

var TOPOLOGY_STATE = {
  data: null,
  panX: 40,
  panY: 30,
  scale: 1.0,
  isDragging: false,
  dragStartX: 0,
  dragStartY: 0,
  selectedNodeId: null,
  filterType: "all",
  onlyIssues: false,
  searchQuery: "",
  nodePositions: {},
  nodeOffsets: {},
  suppressClickUntil: 0,
  initialized: false
};

// Nodes the user dragged keep their place between visits (only a small map of id -> offset)
var TOPOLOGY_OFFSETS_KEY = "mininoc.topology.offsets";
function loadTopologyOffsets() {
  try {
    var raw = window.localStorage.getItem(TOPOLOGY_OFFSETS_KEY);
    var parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (err) {
    return {};
  }
}
function saveTopologyOffsets() {
  try {
    window.localStorage.setItem(TOPOLOGY_OFFSETS_KEY, JSON.stringify(TOPOLOGY_STATE.nodeOffsets));
  } catch (err) {
    /* private window or blocked storage: the offsets last until the page closes */
  }
}
TOPOLOGY_STATE.nodeOffsets = loadTopologyOffsets();

// Undo / redo of the card layout: every drag, keyboard nudge and reset first saves the map of offsets as it was.
var TOPOLOGY_UNDO_LIMIT = 50;
var topologyUndoStack = [];
var topologyRedoStack = [];

function topologySnapshot() {
  return JSON.stringify(TOPOLOGY_STATE.nodeOffsets || {});
}

function pushTopologyUndo(snapshot) {
  topologyUndoStack.push(snapshot || topologySnapshot());
  if (topologyUndoStack.length > TOPOLOGY_UNDO_LIMIT) {
    topologyUndoStack.shift();
  }
  topologyRedoStack = [];
  updateTopologyUndoButtons();
}

function applyTopologyLayout(snapshot) {
  TOPOLOGY_STATE.nodeOffsets = JSON.parse(snapshot);
  saveTopologyOffsets();
  renderTopologyGraph();
}

function undoTopologyLayout() {
  if (topologyUndoStack.length === 0) {
    return false;
  }
  topologyRedoStack.push(topologySnapshot());
  applyTopologyLayout(topologyUndoStack.pop());
  updateTopologyUndoButtons();
  return true;
}

function redoTopologyLayout() {
  if (topologyRedoStack.length === 0) {
    return false;
  }
  topologyUndoStack.push(topologySnapshot());
  applyTopologyLayout(topologyRedoStack.pop());
  updateTopologyUndoButtons();
  return true;
}

function updateTopologyUndoButtons() {
  var undoBtn = document.getElementById("topo-undo");
  var redoBtn = document.getElementById("topo-redo");
  if (undoBtn) {
    undoBtn.disabled = topologyUndoStack.length === 0;
  }
  if (redoBtn) {
    redoBtn.disabled = topologyRedoStack.length === 0;
  }
}

var TOPOLOGY_MIN_SCALE = 0.15;
var TOPOLOGY_MAX_SCALE = 2.5;

// Zoom keeping the point (cx, cy), given in viewport pixels, where it is
function topologyZoomAt(cx, cy, newScale) {
  newScale = Math.max(TOPOLOGY_MIN_SCALE, Math.min(TOPOLOGY_MAX_SCALE, newScale));
  var k = newScale / TOPOLOGY_STATE.scale;
  TOPOLOGY_STATE.panX = cx - (cx - TOPOLOGY_STATE.panX) * k;
  TOPOLOGY_STATE.panY = cy - (cy - TOPOLOGY_STATE.panY) * k;
  TOPOLOGY_STATE.scale = newScale;
  applyTopologyTransform();
}

// Zoom from a button or the keyboard: around the centre of the panel
function topologyZoomCentered(factor) {
  var viewport = document.getElementById("topology-viewport");
  var w = viewport ? viewport.clientWidth : 800;
  var h = viewport ? viewport.clientHeight : 600;
  topologyZoomAt(w / 2, h / 2, TOPOLOGY_STATE.scale * factor);
}

var topologyRenderQueued = false;
function queueTopologyRender() {
  if (topologyRenderQueued) return;
  topologyRenderQueued = true;
  window.requestAnimationFrame(function () {
    topologyRenderQueued = false;
    renderTopologyGraph();
  });
}

// Moves one card by (dx, dy) in map units and redraws it with its links
function nudgeTopologyNode(nodeId, dx, dy) {
  var cur = TOPOLOGY_STATE.nodeOffsets[nodeId] || { x: 0, y: 0 };
  TOPOLOGY_STATE.nodeOffsets[nodeId] = { x: cur.x + dx, y: cur.y + dy };
  queueTopologyRender();
}

async function loadTopology() {
  try {
    var data = await fetchJson("/api/topology");
    TOPOLOGY_STATE.data = data;

    var genUtc = data.summary ? data.summary.generated_utc : Math.floor(Date.now() / 1000);
    setFreshnessBadge("topology-freshness", "ok", genUtc);

    var emptyEl = document.getElementById("topology-empty");
    var svgEl = document.getElementById("topology-svg");

    if (!data.nodes || data.nodes.length === 0) {
      if (emptyEl) emptyEl.classList.remove("hidden");
      if (svgEl) svgEl.classList.add("hidden");
      return;
    }

    if (emptyEl) emptyEl.classList.add("hidden");
    if (svgEl) svgEl.classList.remove("hidden");

    renderTopologyGraph();
  } catch (err) {
    setFreshnessBadge("topology-freshness", "stale", null);
    var el = document.getElementById("topology-empty");
    if (el) {
      el.classList.remove("hidden");
      el.innerHTML = "<p>Failed to load topology: " + escapeHtml(err.message || String(err)) + "</p>";
    }
  }
}

// How many host cards fit in one row of the panel (a phone gets two, a wide screen up to eight)
function topologyHostsPerRow() {
  var viewport = document.getElementById("topology-viewport");
  var w = (viewport && viewport.clientWidth) || window.innerWidth || 1200;
  // From a laptop up everything goes in one row (the map is fitted to the panel anyway); a phone gets two
  if (w >= 900) return 8;
  return Math.max(2, Math.min(8, Math.floor((w - 100) / 198)));
}

function computeTopologyLayout(nodes, links) {
  var positions = {};
  var layers = {
    routers: [],
    subnets: [],
    hosts: [],
    guests: []
  };

  var i = 0;
  for (i = 0; i < nodes.length; i += 1) {
    var n = nodes[i];
    if (n.type === "router") {
      layers.routers.push(n);
    } else if (n.type === "subnet") {
      layers.subnets.push(n);
    } else if (n.type === "guest") {
      layers.guests.push(n);
    } else {
      layers.hosts.push(n);
    }
  }

  // Vertical layer Y anchors
  var Y_ROUTER = 50;
  var Y_SUBNET = 170;
  var Y_HOST = 290;
  var Y_GUEST = 430;

  var CARD_ROUTER_W = 180;
  var CARD_ROUTER_H = 56;
  var CARD_SUBNET_W = 150;
  var CARD_SUBNET_H = 56;
  var CARD_HOST_W = 170;
  var CARD_HOST_H = 56;
  var CARD_GUEST_W = 150;
  var CARD_GUEST_H = 52;
  var GAP_X = 28;

  // The host row is the widest layer: routers and subnets are centred over it so the links fan out evenly
  var perRow = topologyHostsPerRow();
  var hostsInWidestRow = Math.min(layers.hosts.length, perRow);
  var hostRowW = Math.max(0, hostsInWidestRow * (CARD_HOST_W + GAP_X) - GAP_X);
  var hostRows = Math.max(1, Math.ceil(layers.hosts.length / perRow));
  // Balanced rows (7 hosts in two rows are 4 + 3, not 6 + 1)
  perRow = Math.max(1, Math.ceil(layers.hosts.length / hostRows));
  hostsInWidestRow = Math.min(layers.hosts.length, perRow);
  hostRowW = Math.max(0, hostsInWidestRow * (CARD_HOST_W + GAP_X) - GAP_X);
  // Odd rows are shifted half a card so the lines to the second row pass through the gaps of the first, not behind its cards
  var rowStagger = hostRows > 1 ? (CARD_HOST_W + GAP_X) / 2 : 0;
  hostRowW += rowStagger;
  var ROW_STEP = CARD_HOST_H + 34;

  // 1. Position Routers
  var totalRouterW = layers.routers.length * (CARD_ROUTER_W + GAP_X) - GAP_X;
  var routerStartX = 60 + Math.max(0, (hostRowW - totalRouterW) / 2);
  for (i = 0; i < layers.routers.length; i += 1) {
    var r = layers.routers[i];
    positions[r.id] = {
      x: routerStartX + i * (CARD_ROUTER_W + GAP_X),
      y: Y_ROUTER,
      width: CARD_ROUTER_W,
      height: CARD_ROUTER_H,
      node: r
    };
  }

  // 2. Position Subnets
  var totalSubnetW = layers.subnets.length * (CARD_SUBNET_W + GAP_X) - GAP_X;
  var subnetStartX = 60 + Math.max(0, (hostRowW - totalSubnetW) / 2);
  for (i = 0; i < layers.subnets.length; i += 1) {
    var s = layers.subnets[i];
    positions[s.id] = {
      x: subnetStartX + i * (CARD_SUBNET_W + GAP_X),
      y: Y_SUBNET,
      width: CARD_SUBNET_W,
      height: CARD_SUBNET_H,
      node: s
    };
  }

  // 3. Position Hosts / Hypervisors (wrapped in rows; each row is centred under the subnets)
  var totalHostW = hostRowW;
  var hostStartX = 60;
  for (i = 0; i < layers.hosts.length; i += 1) {
    var h = layers.hosts[i];
    var hRow = Math.floor(i / perRow);
    var hCol = i % perRow;
    var inThisRow = Math.min(perRow, layers.hosts.length - hRow * perRow);
    var thisRowW = inThisRow * (CARD_HOST_W + GAP_X) - GAP_X;
    positions[h.id] = {
      x: hostStartX + Math.max(0, (hostRowW - rowStagger - thisRowW) / 2) + (hRow % 2 === 1 ? rowStagger : 0) + hCol * (CARD_HOST_W + GAP_X),
      y: Y_HOST + hRow * ROW_STEP,
      width: CARD_HOST_W,
      height: CARD_HOST_H,
      node: h
    };
  }
  var guestY = Y_GUEST + (hostRows - 1) * ROW_STEP;

  // 4. Position Guests (grouped beneath parent hypervisors)
  // Map parent_id -> list of guests
  var guestsByParent = {};
  var orphanGuests = [];
  for (i = 0; i < layers.guests.length; i += 1) {
    var g = layers.guests[i];
    if (g.parent_id && positions[g.parent_id]) {
      if (!guestsByParent[g.parent_id]) {
        guestsByParent[g.parent_id] = [];
      }
      guestsByParent[g.parent_id].push(g);
    } else {
      orphanGuests.push(g);
    }
  }

  var parentId = "";
  for (parentId in guestsByParent) {
    if (Object.prototype.hasOwnProperty.call(guestsByParent, parentId)) {
      var pPos = positions[parentId];
      var gList = guestsByParent[parentId];
      var totalGW = gList.length * (CARD_GUEST_W + GAP_X) - GAP_X;
      var gStartX = Math.max(30, (pPos.x + pPos.width / 2) - totalGW / 2);

      var k = 0;
      for (k = 0; k < gList.length; k += 1) {
        var guest = gList[k];
        positions[guest.id] = {
          x: gStartX + k * (CARD_GUEST_W + GAP_X),
          y: guestY,
          width: CARD_GUEST_W,
          height: CARD_GUEST_H,
          node: guest
        };
      }
    }
  }

  // Orphan guests
  if (orphanGuests.length > 0) {
    var orphanStartX = Math.max(hostStartX + totalHostW + 40, 60);
    for (i = 0; i < orphanGuests.length; i += 1) {
      var og = orphanGuests[i];
      positions[og.id] = {
        x: orphanStartX + i * (CARD_GUEST_W + GAP_X),
        y: guestY,
        width: CARD_GUEST_W,
        height: CARD_GUEST_H,
        node: og
      };
    }
  }

  // Calculate layout bounds
  var maxX = 800;
  var maxY = 550;
  var nid = "";
  for (nid in positions) {
    if (Object.prototype.hasOwnProperty.call(positions, nid)) {
      var pos = positions[nid];
      if (pos.x + pos.width + 80 > maxX) maxX = pos.x + pos.width + 80;
      if (pos.y + pos.height + 80 > maxY) maxY = pos.y + pos.height + 80;
    }
  }

  return {
    positions: positions,
    bounds: { width: maxX, height: maxY }
  };
}

function getNodeStatusColor(status) {
  if (status === "ok") return "var(--color-ok, #37d492)";
  if (status === "firing") return "var(--color-danger, #ff6762)";
  if (status === "degraded") return "var(--color-warn, #f9b73f)";
  if (status === "maintenance") return "var(--color-purple, #a78bfa)";
  return "var(--text-muted, #94a3b8)";
}

function getNodeTypeBadge(type, meta) {
  if (type === "router") return "ROUTER";
  if (type === "subnet") return "SUBNET";
  if (type === "hypervisor") return "PROXMOX";
  if (type === "guest") {
    if (meta && meta.guest_type) return meta.guest_type.toUpperCase();
    return "VM/CT";
  }
  return "HOST";
}

function renderTopologyGraph() {
  if (!TOPOLOGY_STATE.data) return;

  var rawNodes = TOPOLOGY_STATE.data.nodes || [];
  var subnets = TOPOLOGY_STATE.data.subnets || [];
  var nodes = rawNodes.slice();

  // Add subnets as layer 1 nodes
  var s = 0;
  for (s = 0; s < subnets.length; s += 1) {
    var sub = subnets[s];
    if (!nodes.some(function (n) { return n.id === sub.id; })) {
      nodes.push({
        id: sub.id,
        label: sub.name + " (" + sub.cidr + ")",
        name: sub.name,
        type: "subnet",
        device_type: "subnet",
        status: "ok",
        ip: sub.cidr,
        meta: { interface: sub.interface_name, nodes_count: sub.nodes_count }
      });
    }
  }

  // Normalize nodes
  var i = 0;
  for (i = 0; i < nodes.length; i += 1) {
    var n = nodes[i];
    n.label = n.label || n.name || n.hostname || n.id;
    var dt = n.device_type || n.type || "server";
    if (dt === "proxmox") dt = "hypervisor";
    if (n.guest_info) dt = "guest";
    n.type = n.type || dt;
    n.parent_id = n.parent_id || n.parent_device_id || (n.guest_info ? n.guest_info.hypervisor_id : null);
    if (n.status === "healthy") n.status = "ok";
    if (n.status === "warning") n.status = "degraded";
    if (n.status === "critical") n.status = "firing";
    n.meta = n.meta || n.metrics || n.guest_info || {};
  }

  var rawLinks = TOPOLOGY_STATE.data.links || [];
  var links = [];
  for (i = 0; i < rawLinks.length; i += 1) {
    var rl = rawLinks[i];
    links.push({
      source: rl.source,
      target: rl.target,
      type: rl.type || rl.link_type || "interface",
      label: rl.label || ""
    });
  }

  var layout = computeTopologyLayout(nodes, links);
  var savedOffsets = TOPOLOGY_STATE.nodeOffsets || {};
  Object.keys(layout.positions).forEach(function (pid) {
    var off = savedOffsets[pid];
    if (off && isFinite(off.x) && isFinite(off.y)) {
      layout.positions[pid].x += off.x;
      layout.positions[pid].y += off.y;
    }
  });
  TOPOLOGY_STATE.nodePositions = layout.positions;

  var layerBackdrops = document.getElementById("topo-layer-backdrops");
  var layerLinks = document.getElementById("topo-layer-links");
  var layerNodes = document.getElementById("topo-layer-nodes");

  if (!layerBackdrops || !layerLinks || !layerNodes) return;

  layerBackdrops.innerHTML = "";
  layerLinks.innerHTML = "";
  layerNodes.innerHTML = "";

  // 1. Render Hypervisor cluster backdrops
  var pveNodes = nodes.filter(function (n) { return n.type === "hypervisor"; });
  var b = 0;
  for (b = 0; b < pveNodes.length; b += 1) {
    var pve = pveNodes[b];
    var pvePos = layout.positions[pve.id];
    if (pvePos) {
      // Find guest positions
      var guestPosList = [];
      var g = 0;
      for (g = 0; g < nodes.length; g += 1) {
        if (nodes[g].parent_id === pve.id && layout.positions[nodes[g].id]) {
          guestPosList.push(layout.positions[nodes[g].id]);
        }
      }

      if (guestPosList.length > 0) {
        var minX = pvePos.x - 20;
        var maxX = pvePos.x + pvePos.width + 20;
        var minY = pvePos.y - 25;
        var maxY = pvePos.y + pvePos.height + 20;

        for (g = 0; g < guestPosList.length; g += 1) {
          var gp = guestPosList[g];
          if (gp.x - 16 < minX) minX = gp.x - 16;
          if (gp.x + gp.width + 16 > maxX) maxX = gp.x + gp.width + 16;
          if (gp.y + gp.height + 20 > maxY) maxY = gp.y + gp.height + 20;
        }

        var bdW = maxX - minX;
        var bdH = maxY - minY;

        var bdGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
        bdGroup.innerHTML =
          '<rect class="topo-backdrop-rect" x="' + minX + '" y="' + minY + '" width="' + bdW + '" height="' + bdH + '" rx="8"></rect>' +
          '<text class="topo-backdrop-label" x="' + (minX + 12) + '" y="' + (minY + 16) + '">PROXMOX CLUSTER / ' + escapeHtml((pve.meta && pve.meta.node_name) || pve.label) + '</text>';
        layerBackdrops.appendChild(bdGroup);
      }
    }
  }

  // 2. Render Links
  var l = 0;
  for (l = 0; l < links.length; l += 1) {
    var link = links[l];
    var sPos = layout.positions[link.source];
    var tPos = layout.positions[link.target];

    if (sPos && tPos) {
      var sx = sPos.x + sPos.width / 2;
      var sy = sPos.y + sPos.height;
      var tx = tPos.x + tPos.width / 2;
      var ty = tPos.y;

      if (sPos.y > tPos.y) {
        sy = sPos.y;
        ty = tPos.y + tPos.height;
      }

      var midY = (sy + ty) / 2;
      var d = "M " + sx + " " + sy + " C " + sx + " " + midY + ", " + tx + " " + midY + ", " + tx + " " + ty;

      var pathEl = document.createElementNS("http://www.w3.org/2000/svg", "path");
      pathEl.setAttribute("d", d);
      var linkClass = "topo-link link-" + (link.type || "interface");
      pathEl.setAttribute("class", linkClass);
      pathEl.setAttribute("data-source", link.source);
      pathEl.setAttribute("data-target", link.target);

      if (link.type === "dependency") {
        pathEl.setAttribute("marker-end", "url(#topo-arrow-dep)");
      } else if (link.type === "hypervisor") {
        pathEl.setAttribute("marker-end", "url(#topo-arrow-hyp)");
      }

      layerLinks.appendChild(pathEl);
    }
  }

  // 3. Render Nodes
  var nIdx = 0;
  for (nIdx = 0; nIdx < nodes.length; nIdx += 1) {
    var node = nodes[nIdx];
    var pos = layout.positions[node.id];
    if (pos) {
      var nodeGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
      var isSelected = TOPOLOGY_STATE.selectedNodeId === node.id;
      nodeGroup.setAttribute("class", "topo-node" + (isSelected ? " selected" : ""));
      nodeGroup.setAttribute("data-node-id", node.id);
      nodeGroup.setAttribute("tabindex", "0");
      nodeGroup.setAttribute("role", "button");
      nodeGroup.setAttribute("aria-label", node.label + " (" + node.type + ")");

      var stColor = getNodeStatusColor(node.status);
      var badgeText = getNodeTypeBadge(node.type, node.meta);
      var subText = node.ip || (node.meta && node.meta.vmid ? "VMID " + node.meta.vmid : "");
      var titleText = node.label;
      if (node.type === "subnet") {
        // The card shows the name and, below it, the range (the full "name (range)" is still the drawer title)
        titleText = node.name || node.label;
        subText = node.ip || (node.meta && node.meta.interface) || "Subnet Bus";
      }
      // Text never leaves its card: cut with an ellipsis (about 6.4 px per character) and keep the full text as a tooltip
      var maxChars = Math.max(6, Math.floor((pos.width - 26) / 6.4));
      var fitText = function (t) { t = String(t || ""); return t.length > maxChars ? t.slice(0, maxChars - 1) + "…" : t; };

      var alertBadgeSvg = "";
      if (node.active_alerts_count && node.active_alerts_count > 0) {
        // Flush with the right edge of the card and centred on its top border (K126): 2 px short of the edge it sat on the
        // start of the rounded corner, which read as almost aligned but not quite.
        var abX = pos.x + pos.width - 22;
        var abY = pos.y - 7.5;
        alertBadgeSvg =
          '<g class="topo-node-alert-badge">' +
          '<rect x="' + abX + '" y="' + abY + '" width="22" height="15" rx="7.5" fill="var(--color-danger, #ff6762)"></rect>' +
          '<text x="' + (abX + 11) + '" y="' + (abY + 7.5) + '" fill="#ffffff" font-size="9" font-weight="700" text-anchor="middle" dominant-baseline="central">' + node.active_alerts_count + '</text>' +
          '</g>';
      }

      var cardInner =
        '<rect class="topo-node-card" x="' + pos.x + '" y="' + pos.y + '" width="' + pos.width + '" height="' + pos.height + '" rx="6" ry="6"></rect>' +
        '<circle class="topo-node-dot" cx="' + (pos.x + 16) + '" cy="' + (pos.y + 14) + '" r="4.5" fill="' + stColor + '"></circle>' +
        '<text x="' + (pos.x + 27) + '" y="' + (pos.y + 14) + '" class="topo-node-badge" fill="var(--text-muted)">' + escapeHtml(badgeText) + '</text>' +
        '<text x="' + (pos.x + 12) + '" y="' + (pos.y + 30) + '" class="topo-node-title">' + escapeHtml(fitText(titleText)) + '</text>' +
        '<text x="' + (pos.x + 12) + '" y="' + (pos.y + 45) + '" class="topo-node-sub">' + escapeHtml(fitText(subText)) + '</text>' +
        alertBadgeSvg;

      nodeGroup.innerHTML = '<title>' + escapeHtml(node.label + (node.ip ? " · " + node.ip : "")) + '</title>' + cardInner;

      // Event listener for selection
      (function (nid) {
        nodeGroup.addEventListener("click", function (ev) {
          ev.stopPropagation();
          if (Date.now() < TOPOLOGY_STATE.suppressClickUntil) return;
          selectTopologyNode(nid);
        });
        nodeGroup.addEventListener("keydown", function (ev) {
          if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            selectTopologyNode(nid);
            return;
          }
          // Shift + arrows move the focused card (the keyboard equivalent of dragging it)
          var step = ev.key === "ArrowLeft" ? [-20, 0] : ev.key === "ArrowRight" ? [20, 0] : ev.key === "ArrowUp" ? [0, -20] : ev.key === "ArrowDown" ? [0, 20] : null;
          if (step && ev.shiftKey) {
            ev.preventDefault();
            ev.stopPropagation();
            pushTopologyUndo();
            nudgeTopologyNode(nid, step[0], step[1]);
            saveTopologyOffsets();
            window.setTimeout(function () {
              var again = document.querySelector('.topo-node[data-node-id="' + String(nid).replace(/"/g, "") + '"]');
              if (again) again.focus();
            }, 40);
          }
        });
      })(node.id);

      layerNodes.appendChild(nodeGroup);
    }
  }

  applyTopologyTransform();
  applyTopologyFilters();
  // First draw: fit the map to the panel once (the tab may have been hidden while the data loaded)
  if (!TOPOLOGY_STATE.autoFitted) {
    TOPOLOGY_STATE.autoFitted = true;
    window.setTimeout(fitTopologyView, 80);
  }

  if (TOPOLOGY_STATE.selectedNodeId) {
    selectTopologyNode(TOPOLOGY_STATE.selectedNodeId);
  }
}

function applyTopologyTransform() {
  var rootGroup = document.getElementById("topology-root-group");
  if (rootGroup) {
    var transform = "translate(" + TOPOLOGY_STATE.panX + ", " + TOPOLOGY_STATE.panY + ") scale(" + TOPOLOGY_STATE.scale + ")";
    rootGroup.setAttribute("transform", transform);
  }
}

// With nodes on the map but none of them matching, a banner says why and offers to undo it.
function updateTopologyNoMatch(nodes, matching, query) {
  var box = document.getElementById("topo-no-match");
  var text = document.getElementById("topo-no-match-text");
  if (!box || !text) {
    return;
  }
  var anyMatch = false;
  var i = 0;
  for (i = 0; i < nodes.length; i += 1) {
    if (matching[nodes[i].id]) {
      anyMatch = true;
    }
  }
  var narrowed = query !== "" || TOPOLOGY_STATE.filterType !== "all" || TOPOLOGY_STATE.onlyIssues;
  var show = nodes.length > 0 && !anyMatch && narrowed;
  box.classList.toggle("hidden", !show);
  if (show) {
    var parts = [];
    if (query !== "") {
      parts.push("the search \u201c" + query + "\u201d");
    }
    if (TOPOLOGY_STATE.filterType !== "all" || TOPOLOGY_STATE.onlyIssues) {
      parts.push("the filter");
    }
    text.textContent = "No device matches " + parts.join(" and ") + ".";
  }
}

function clearTopologyFilters() {
  TOPOLOGY_STATE.searchQuery = "";
  TOPOLOGY_STATE.filterType = "all";
  TOPOLOGY_STATE.onlyIssues = false;
  var searchInput = document.getElementById("topo-search-input");
  if (searchInput) {
    searchInput.value = "";
  }
  var pills = document.querySelectorAll("[data-topo-filter]");
  var p = 0;
  for (p = 0; p < pills.length; p += 1) {
    var isAll = pills[p].getAttribute("data-topo-filter") === "all";
    pills[p].classList.toggle("active", isAll);
    pills[p].setAttribute("aria-pressed", isAll ? "true" : "false");
  }
  var issuesBtn = document.getElementById("topo-filter-issues-btn");
  if (issuesBtn) {
    issuesBtn.setAttribute("aria-pressed", "false");
  }
  applyTopologyFilters();
}

function applyTopologyFilters() {
  var query = (TOPOLOGY_STATE.searchQuery || "").trim().toLowerCase();
  var filter = TOPOLOGY_STATE.filterType || "all";
  var onlyIssues = TOPOLOGY_STATE.onlyIssues;

  var nodes = TOPOLOGY_STATE.data ? TOPOLOGY_STATE.data.nodes || [] : [];
  var nodeEls = document.querySelectorAll(".topo-node");
  var linkEls = document.querySelectorAll(".topo-link");

  var matchingNodeIds = {};

  var i = 0;
  for (i = 0; i < nodes.length; i += 1) {
    var n = nodes[i];
    var matchQuery = true;
    if (query) {
      var lbl = (n.label || "").toLowerCase();
      var ip = (n.ip || "").toLowerCase();
      var id = (n.id || "").toLowerCase();
      var vmid = (n.meta && n.meta.vmid ? String(n.meta.vmid) : "");
      matchQuery = lbl.indexOf(query) !== -1 || ip.indexOf(query) !== -1 || id.indexOf(query) !== -1 || vmid.indexOf(query) !== -1;
    }

    var matchFilter = true;
    if (filter === "router") matchFilter = n.type === "router";
    else if (filter === "proxmox") matchFilter = n.type === "hypervisor";
    else if (filter === "guest") matchFilter = n.type === "guest";
    else if (filter === "host") matchFilter = n.type === "server";

    var matchIssues = true;
    if (onlyIssues) {
      matchIssues = (n.status !== "ok" && n.status !== "unknown") || (n.active_alerts_count && n.active_alerts_count > 0);
    }

    if (matchQuery && matchFilter && matchIssues) {
      matchingNodeIds[n.id] = true;
    }
  }

  // Subnets are drawn by the layout (id "subnet:...") and are not in the node list: they are structure, never dimmed
  for (i = 0; i < nodeEls.length; i += 1) {
    var sid = nodeEls[i].getAttribute("data-node-id") || "";
    if (sid.indexOf("subnet:") === 0) matchingNodeIds[sid] = true;
  }

  for (i = 0; i < nodeEls.length; i += 1) {
    var el = nodeEls[i];
    var nid = el.getAttribute("data-node-id");
    if (matchingNodeIds[nid]) {
      el.classList.remove("node-dimmed");
    } else {
      el.classList.add("node-dimmed");
    }
  }

  updateTopologyNoMatch(nodes, matchingNodeIds, query);

  for (i = 0; i < linkEls.length; i += 1) {
    var lEl = linkEls[i];
    var src = lEl.getAttribute("data-source");
    var tgt = lEl.getAttribute("data-target");
    if (matchingNodeIds[src] && matchingNodeIds[tgt]) {
      lEl.classList.remove("link-dimmed");
    } else {
      lEl.classList.add("link-dimmed");
    }
  }
}

function selectTopologyNode(nodeId) {
  TOPOLOGY_STATE.selectedNodeId = nodeId;

  var nodeEls = document.querySelectorAll(".topo-node");
  var linkEls = document.querySelectorAll(".topo-link");
  var drawer = document.getElementById("topology-drawer");

  if (!nodeId) {
    if (drawer) drawer.classList.add("hidden");
    var j = 0;
    for (j = 0; j < nodeEls.length; j += 1) nodeEls[j].classList.remove("selected");
    for (j = 0; j < linkEls.length; j += 1) linkEls[j].classList.remove("highlighted");
    return;
  }

  var node = null;
  if (TOPOLOGY_STATE.nodePositions && TOPOLOGY_STATE.nodePositions[nodeId]) {
    node = TOPOLOGY_STATE.nodePositions[nodeId].node;
  } else if (TOPOLOGY_STATE.data && TOPOLOGY_STATE.data.nodes) {
    node = TOPOLOGY_STATE.data.nodes.find(function (n) { return n.id === nodeId; });
  }

  if (!node) {
    if (drawer) drawer.classList.add("hidden");
    return;
  }

  // Update SVG node classes
  var i = 0;
  for (i = 0; i < nodeEls.length; i += 1) {
    if (nodeEls[i].getAttribute("data-node-id") === nodeId) {
      nodeEls[i].classList.add("selected");
    } else {
      nodeEls[i].classList.remove("selected");
    }
  }

  // Highlight connected links
  for (i = 0; i < linkEls.length; i += 1) {
    var src = linkEls[i].getAttribute("data-source");
    var tgt = linkEls[i].getAttribute("data-target");
    if (src === nodeId || tgt === nodeId) {
      linkEls[i].classList.add("highlighted");
    } else {
      linkEls[i].classList.remove("highlighted");
    }
  }

  // Populate drawer
  if (!drawer) return;
  drawer.classList.remove("hidden");

  var typeBadge = document.getElementById("drawer-node-type-badge");
  var nodeName = document.getElementById("drawer-node-name");
  var nodeIp = document.getElementById("drawer-node-ip");
  var stDot = document.getElementById("drawer-status-dot");
  var stText = document.getElementById("drawer-status-text");
  var stDesc = document.getElementById("drawer-status-desc");
  var metricsList = document.getElementById("drawer-metrics-list");
  var relations = document.getElementById("drawer-relations");
  var alertsList = document.getElementById("drawer-alerts-list");
  var viewOverviewBtn = document.getElementById("drawer-view-overview-btn");

  if (typeBadge) {
    var bText = getNodeTypeBadge(node.type, node.meta);
    var v = (node.meta && node.meta.vendor) || node.vendor;
    if (v && v !== "Generic") {
      bText += " · " + v;
    }
    typeBadge.textContent = bText;
  }
  if (nodeName) nodeName.textContent = node.label;
  if (nodeIp) nodeIp.textContent = node.ip || (node.meta && node.meta.vmid ? "VMID " + node.meta.vmid : "No IP configured");

  if (stDot) stDot.style.background = getNodeStatusColor(node.status);
  if (stText) stText.textContent = (node.status || "unknown").toUpperCase();
  if (stDesc) {
    if (node.status === "ok") stDesc.textContent = "Node is operational and responding to health polls.";
    else if (node.status === "firing") stDesc.textContent = (node.active_alerts_count || 1) + " alert(s) currently firing on this target.";
    else if (node.status === "maintenance") stDesc.textContent = "Target is under a scheduled maintenance window.";
    else if (node.status === "degraded") stDesc.textContent = "Metrics or dependent upstream services degraded.";
    else stDesc.textContent = "Status unknown or target not actively polled.";
  }

  // Telemetry KPIs
  if (metricsList) {
    var meta = node.meta || {};
    var dtHtml =
      "<dt>Type</dt><dd>" + escapeHtml(node.type) + "</dd>" +
      "<dt>Health Score</dt><dd>" + escapeHtml(node.health_score !== undefined ? node.health_score + " / 100" : "100 / 100") + "</dd>";

    if (meta.vmid) {
      dtHtml += "<dt>VMID</dt><dd>" + escapeHtml(meta.vmid) + "</dd>";
      dtHtml += "<dt>Virtual Type</dt><dd>" + escapeHtml(meta.guest_type || "N/A") + "</dd>";
      dtHtml += "<dt>Guest State</dt><dd>" + escapeHtml(meta.status || "running") + "</dd>";
    }
    if (typeof meta.cpu_pct === "number") {
      dtHtml += "<dt>CPU Usage</dt><dd>" + escapeHtml(meta.cpu_pct.toFixed(1)) + " %</dd>";
    }
    if (typeof meta.mem_pct === "number") {
      dtHtml += "<dt>RAM Usage</dt><dd>" + escapeHtml(meta.mem_pct.toFixed(1)) + " %</dd>";
    }
    if (meta.node_name) {
      dtHtml += "<dt>Hypervisor Node</dt><dd>" + escapeHtml(meta.node_name) + "</dd>";
    }
    if (meta.interface_count) {
      dtHtml += "<dt>Active Interfaces</dt><dd>" + escapeHtml(meta.interface_count) + "</dd>";
    }
    if (meta.device_id) {
      dtHtml += "<dt>Device ID</dt><dd>" + escapeHtml(meta.device_id) + "</dd>";
    }
    if (meta.services && meta.services.length > 0) {
      var sHtml = "<div style='display:flex; flex-wrap:wrap; gap:4px; margin-top:2px;'>";
      var sIdx = 0;
      for (sIdx = 0; sIdx < meta.services.length; sIdx += 1) {
        var sItem = meta.services[sIdx];
        var sBadgeCls = sItem.state === "active" ? "badge-ok" : (sItem.state === "failed" ? "badge-down" : "badge-subtle");
        sHtml += "<span class='badge " + sBadgeCls + "' style='font-size:10px; padding:1px 5px;' title='" + escapeHtml((sItem.substate || sItem.state)) + "'>" + escapeHtml(sItem.service_name) + "</span>";
      }
      sHtml += "</div>";
      dtHtml += "<dt>Monitored Services</dt><dd>" + sHtml + "</dd>";
    }
    metricsList.innerHTML = dtHtml;
  }

  // Hierarchy & Relations
  if (relations) {
    var relHtml = "";
    if (node.parent_id) {
      var pNode = TOPOLOGY_STATE.data.nodes.find(function (n) { return n.id === node.parent_id; });
      var pLabel = pNode ? pNode.label : node.parent_id;
      relHtml += "<div class='relation-item'><span>Parent Hypervisor:</span> <strong>" + escapeHtml(pLabel) + "</strong></div>";
    }

    // Children
    var children = TOPOLOGY_STATE.data.nodes.filter(function (n) { return n.parent_id === node.id; });
    if (children.length > 0) {
      var chLabels = children.map(function (c) { return escapeHtml(c.label); }).join(", ");
      relHtml += "<div class='relation-item'><span>Hosted Guests (" + children.length + "):</span> <div>" + chLabels + "</div></div>";
    }

    // Connected links
    var connected = (TOPOLOGY_STATE.data.links || []).filter(function (l) { return l.source === node.id || l.target === node.id; });
    relHtml += "<div class='relation-item'><span>Connections:</span> " + connected.length + " links</div>";

    relations.innerHTML = relHtml;
  }

  // Active Alerts
  if (alertsList) {
    var metaAlerts = (node.meta && node.meta.active_alerts) || [];
    if (metaAlerts.length === 0) {
      alertsList.innerHTML = "<p class='text-muted' style='margin: 0; font-size: var(--fs-xs);'>No active alerts.</p>";
    } else {
      var aHtml = "";
      var a = 0;
      for (a = 0; a < metaAlerts.length; a += 1) {
        var alt = metaAlerts[a];
        aHtml +=
          "<div class='drawer-alert-item'>" +
          "<strong>" + escapeHtml(alt.rule_id || "ALERT") + "</strong> (" + escapeHtml(alt.severity || "warning") + ")<br>" +
          "<span>" + escapeHtml(alt.summary || alt.message || "Alert active") + "</span>" +
          "</div>";
      }
      alertsList.innerHTML = aHtml;
    }
  }

  // Inspect in Overview Button
  if (viewOverviewBtn) {
    viewOverviewBtn.onclick = function () {
      if (node.type === "router" || (node.meta && node.meta.device_id)) {
        var targetDev = (node.meta && node.meta.device_id) || "edge-router-1";
        STATE.deviceId = targetDev;
        switchTab("tab-overview");
        loadOverviewAndDevice();
      } else if (node.parent_id && TOPOLOGY_STATE.data) {
        var parentDev = TOPOLOGY_STATE.data.nodes.find(function (n) { return n.id === node.parent_id; });
        if (parentDev && parentDev.meta && parentDev.meta.device_id) {
          STATE.deviceId = parentDev.meta.device_id;
          switchTab("tab-overview");
          loadOverviewAndDevice();
        } else {
          switchTab("tab-overview");
        }
      } else {
        switchTab("tab-overview");
      }
    };
  }
}

function initTopologyControls() {
  if (TOPOLOGY_STATE.initialized) return;
  TOPOLOGY_STATE.initialized = true;

  var viewport = document.getElementById("topology-viewport");
  var searchInput = document.getElementById("topo-search-input");
  var filterButtons = document.querySelectorAll("[data-topo-filter]");
  var issuesBtn = document.getElementById("topo-filter-issues-btn");
  var refreshBtn = document.getElementById("topology-refresh-btn");
  var closeDrawerBtn = document.getElementById("drawer-close-btn");

  var zoomInBtn = document.getElementById("topo-zoom-in");
  var zoomOutBtn = document.getElementById("topo-zoom-out");
  var zoomFitBtn = document.getElementById("topo-zoom-fit");
  var zoomResetBtn = document.getElementById("topo-zoom-reset");

  // Pointer events cover mouse, pen and touch: one pointer drags the map (or a card), two pinch to zoom
  if (viewport) {
    var pointers = {};
    var gesture = null;

    var pointerCount = function () { return Object.keys(pointers).length; };
    var pinchState = function () {
      var ids = Object.keys(pointers);
      var pa = pointers[ids[0]];
      var pc = pointers[ids[1]];
      return { dist: Math.hypot(pa.x - pc.x, pa.y - pc.y) || 1, cx: (pa.x + pc.x) / 2, cy: (pa.y + pc.y) / 2 };
    };

    viewport.addEventListener("pointerdown", function (e) {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
      if (pointerCount() === 2) {
        var ps0 = pinchState();
        gesture = { type: "pinch", dist: ps0.dist, scale: TOPOLOGY_STATE.scale, moved: true };
        viewport.classList.remove("dragging");
        return;
      }
      var nodeEl = e.target.closest && e.target.closest(".topo-node");
      if (nodeEl) {
        var nid = nodeEl.getAttribute("data-node-id");
        var base = TOPOLOGY_STATE.nodeOffsets[nid] || { x: 0, y: 0 };
        gesture = { type: "node", id: nid, startX: e.clientX, startY: e.clientY, baseX: base.x, baseY: base.y, moved: false, before: topologySnapshot() };
      } else {
        gesture = { type: "pan", startX: e.clientX - TOPOLOGY_STATE.panX, startY: e.clientY - TOPOLOGY_STATE.panY, moved: false };
        TOPOLOGY_STATE.isDragging = true;
        viewport.classList.add("dragging");
      }
    });

    window.addEventListener("pointermove", function (e) {
      if (!pointers[e.pointerId] || !gesture) return;
      pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
      if (gesture.type === "pinch") {
        if (pointerCount() !== 2) return;
        var ps = pinchState();
        var rect = viewport.getBoundingClientRect();
        topologyZoomAt(ps.cx - rect.left, ps.cy - rect.top, gesture.scale * (ps.dist / gesture.dist));
      } else if (gesture.type === "pan") {
        var nx = e.clientX - gesture.startX;
        var ny = e.clientY - gesture.startY;
        if (Math.abs(nx - TOPOLOGY_STATE.panX) + Math.abs(ny - TOPOLOGY_STATE.panY) > 0) gesture.moved = true;
        TOPOLOGY_STATE.panX = nx;
        TOPOLOGY_STATE.panY = ny;
        applyTopologyTransform();
      } else if (gesture.type === "node") {
        var dx = e.clientX - gesture.startX;
        var dy = e.clientY - gesture.startY;
        if (!gesture.moved && Math.hypot(dx, dy) < 5) return;
        if (!gesture.moved) pushTopologyUndo(gesture.before);
        gesture.moved = true;
        TOPOLOGY_STATE.nodeOffsets[gesture.id] = { x: gesture.baseX + dx / TOPOLOGY_STATE.scale, y: gesture.baseY + dy / TOPOLOGY_STATE.scale };
        queueTopologyRender();
      }
    });

    var endPointer = function (e) {
      if (!pointers[e.pointerId]) return;
      delete pointers[e.pointerId];
      if (gesture && gesture.moved) {
        // the click that follows a drag must not select anything
        TOPOLOGY_STATE.suppressClickUntil = Date.now() + 80;
        if (gesture.type === "node") saveTopologyOffsets();
      }
      if (pointerCount() === 0 || (gesture && gesture.type === "pinch")) {
        gesture = null;
        TOPOLOGY_STATE.isDragging = false;
        viewport.classList.remove("dragging");
      }
    };
    window.addEventListener("pointerup", endPointer);
    window.addEventListener("pointercancel", endPointer);

    // Wheel (and a trackpad pinch, which arrives as ctrl + wheel) zooms around the cursor
    viewport.addEventListener("wheel", function (e) {
      e.preventDefault();
      var wrect = viewport.getBoundingClientRect();
      var factor = e.deltaY < 0 ? 1.1 : 0.9;
      topologyZoomAt(e.clientX - wrect.left, e.clientY - wrect.top, TOPOLOGY_STATE.scale * factor);
    }, { passive: false });

    viewport.addEventListener("click", function (e) {
      if (Date.now() < TOPOLOGY_STATE.suppressClickUntil) return;
      if (e.target.closest && !e.target.closest(".topo-node") && !e.target.closest("#topology-drawer")) {
        selectTopologyNode(null);
      }
    });

    // Keyboard on the focused map: arrows pan, + and - zoom, 0 fits to the panel
    viewport.addEventListener("keydown", function (e) {
      if (e.target !== viewport) return;
      var step = 48;
      if (e.key === "ArrowLeft") TOPOLOGY_STATE.panX += step;
      else if (e.key === "ArrowRight") TOPOLOGY_STATE.panX -= step;
      else if (e.key === "ArrowUp") TOPOLOGY_STATE.panY += step;
      else if (e.key === "ArrowDown") TOPOLOGY_STATE.panY -= step;
      else if (e.key === "+" || e.key === "=") { e.preventDefault(); topologyZoomCentered(1.2); return; }
      else if (e.key === "-" || e.key === "_") { e.preventDefault(); topologyZoomCentered(1 / 1.2); return; }
      else if (e.key === "0") { e.preventDefault(); fitTopologyView(); return; }
      else return;
      e.preventDefault();
      applyTopologyTransform();
    });
  }

  // Zoom buttons (around the centre of the panel)
  if (zoomInBtn) zoomInBtn.addEventListener("click", function () { topologyZoomCentered(1.2); });
  if (zoomOutBtn) zoomOutBtn.addEventListener("click", function () { topologyZoomCentered(1 / 1.2); });
  if (zoomFitBtn) zoomFitBtn.addEventListener("click", function () { fitTopologyView(); });
  var undoBtn = document.getElementById("topo-undo");
  var redoBtn = document.getElementById("topo-redo");
  if (undoBtn) undoBtn.addEventListener("click", undoTopologyLayout);
  if (redoBtn) redoBtn.addEventListener("click", redoTopologyLayout);
  updateTopologyUndoButtons();
  // Ctrl+Z / Cmd+Z undoes a move of the cards, Ctrl+Shift+Z or Ctrl+Y redoes it (only on the Topology view, and never
  // while typing in a field: there the browser's own undo of the text must win).
  document.addEventListener("keydown", function (e) {
    if (!(e.ctrlKey || e.metaKey) || e.altKey || STATE.activeTab !== "tab-topology") return;
    var tag = e.target && e.target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (e.target && e.target.isContentEditable)) return;
    var key = String(e.key).toLowerCase();
    var done = false;
    if (key === "z" && !e.shiftKey) done = undoTopologyLayout();
    else if ((key === "z" && e.shiftKey) || key === "y") done = redoTopologyLayout();
    else return;
    e.preventDefault();
    if (done) showToast(key === "z" && !e.shiftKey ? "Undid the last move" : "Redid the move");
  });
  // Reset: back to the automatic layout (forgets dragged cards) and fitted to the panel
  if (zoomResetBtn) {
    zoomResetBtn.addEventListener("click", function () {
      pushTopologyUndo();
      TOPOLOGY_STATE.nodeOffsets = {};
      saveTopologyOffsets();
      renderTopologyGraph();
      fitTopologyView();
    });
  }

  var noMatchBtn = document.querySelector("#topo-no-match button");
  if (noMatchBtn) {
    noMatchBtn.addEventListener("click", clearTopologyFilters);
  }

  // Search input
  if (searchInput) {
    searchInput.addEventListener("input", function () {
      TOPOLOGY_STATE.searchQuery = searchInput.value;
      applyTopologyFilters();
    });
  }

  // Type filter pills
  var b = 0;
  for (b = 0; b < filterButtons.length; b += 1) {
    (function (btn) {
      btn.addEventListener("click", function () {
        var k = 0;
        for (k = 0; k < filterButtons.length; k += 1) {
          filterButtons[k].classList.remove("active");
          filterButtons[k].setAttribute("aria-pressed", "false");
        }
        btn.classList.add("active");
        btn.setAttribute("aria-pressed", "true");
        TOPOLOGY_STATE.filterType = btn.getAttribute("data-topo-filter");
        applyTopologyFilters();
      });
    })(filterButtons[b]);
  }

  // Issues toggle button
  if (issuesBtn) {
    issuesBtn.addEventListener("click", function () {
      TOPOLOGY_STATE.onlyIssues = !TOPOLOGY_STATE.onlyIssues;
      issuesBtn.setAttribute("aria-pressed", String(TOPOLOGY_STATE.onlyIssues));
      applyTopologyFilters();
    });
  }

  // Refresh button
  if (refreshBtn) {
    refreshBtn.addEventListener("click", function () {
      loadTopology();
    });
  }

  // Drawer close button
  if (closeDrawerBtn) {
    closeDrawerBtn.addEventListener("click", function () {
      selectTopologyNode(null);
    });
  }

  // Escape key closes drawer
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && TOPOLOGY_STATE.selectedNodeId) {
      selectTopologyNode(null);
    }
  });
}

function fitTopologyView() {
  var viewport = document.getElementById("topology-viewport");
  if (!viewport || !TOPOLOGY_STATE.data || !TOPOLOGY_STATE.data.nodes || TOPOLOGY_STATE.data.nodes.length === 0) return;

  var layout = computeTopologyLayout(TOPOLOGY_STATE.data.nodes, TOPOLOGY_STATE.data.links || []);
  var bounds = layout.bounds;

  var vw = viewport.clientWidth || 800;
  var vh = viewport.clientHeight || 600;

  var scaleX = (vw - 80) / bounds.width;
  var scaleY = (vh - 80) / bounds.height;
  var fitScale = Math.min(1.2, Math.max(TOPOLOGY_MIN_SCALE, Math.min(scaleX, scaleY)));

  TOPOLOGY_STATE.scale = fitScale;
  TOPOLOGY_STATE.panX = Math.max(20, (vw - bounds.width * fitScale) / 2);
  TOPOLOGY_STATE.panY = Math.max(20, (vh - bounds.height * fitScale) / 2);
  applyTopologyTransform();
}

