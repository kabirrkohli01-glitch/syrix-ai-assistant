"use strict";

/*
 * Loopback-only, DNS-pinning HTTP CONNECT proxy for SYRIX's embedded browser.
 *
 * A BrowserView navigation hook sees only the main frame and cannot stop a
 * public page from issuing an XHR to a LAN service.  Resolving a hostname in a
 * hook and then letting Chromium resolve it again also leaves a DNS-rebinding
 * race.  This proxy is the network trust boundary: it validates every HTTP
 * request / HTTPS tunnel, resolves once, and connects to that exact address.
 */

const dns = require("dns");
const http = require("http");
const net = require("net");
const os = require("os");

const DEFAULT_DNS_TIMEOUT_MS = 4_000;
const DEFAULT_CONNECT_TIMEOUT_MS = 15_000;
const DEFAULT_MAX_HTTP_BYTES = 64 * 1024 * 1024;
const DEFAULT_MAX_TUNNEL_BYTES = 2 * 1024 * 1024 * 1024;
const DEFAULT_TUNNEL_IDLE_MS = 5 * 60 * 1000;

const BLOCKED_HOST_SUFFIXES = [
  ".localhost",
  ".local",
  ".localdomain",
  ".internal",
  ".lan",
  ".home",
  ".corp",
];

const BLOCKED_HOSTS = new Set([
  "localhost",
  "localhost.localdomain",
  "metadata.google.internal",
  "metadata",
]);

const BLOCKED_PUBLIC_PORTS = new Set([
  1080, 2049, 2375, 2376, 3306, 3389, 5432, 5900, 5984, 5985, 5986,
  6379, 6443, 9200, 9300, 11211, 27017,
]);

function _normaliseHost(raw) {
  let host = String(raw || "").trim().toLowerCase();
  if (host.startsWith("[") && host.endsWith("]")) host = host.slice(1, -1);
  while (host.endsWith(".")) host = host.slice(0, -1);
  return host;
}

function _parseIpv4(raw) {
  const parts = String(raw || "").split(".");
  if (parts.length !== 4) return null;
  const out = [];
  for (const part of parts) {
    if (!/^\d{1,3}$/.test(part)) return null;
    const value = Number(part);
    if (!Number.isInteger(value) || value < 0 || value > 255) return null;
    out.push(value);
  }
  return out;
}

function _ipv6Words(raw) {
  let value = String(raw || "").toLowerCase().split("%")[0];
  const dotted = value.match(/(\d{1,3}(?:\.\d{1,3}){3})$/);
  if (dotted) {
    const octets = _parseIpv4(dotted[1]);
    if (!octets) return null;
    value = value.slice(0, -dotted[1].length) +
      `${((octets[0] << 8) | octets[1]).toString(16)}:${((octets[2] << 8) | octets[3]).toString(16)}`;
  }
  if ((value.match(/::/g) || []).length > 1) return null;
  const sides = value.split("::");
  const left = sides[0] ? sides[0].split(":") : [];
  const right = sides.length > 1 && sides[1] ? sides[1].split(":") : [];
  if (sides.length === 1 && left.length !== 8) return null;
  if (sides.length === 2 && left.length + right.length >= 8) return null;
  const missing = sides.length === 2 ? 8 - left.length - right.length : 0;
  const parts = left.concat(new Array(missing).fill("0"), right);
  if (parts.length !== 8 || parts.some((p) => !/^[0-9a-f]{1,4}$/.test(p))) return null;
  return parts.map((p) => Number.parseInt(p, 16));
}

function _embeddedIpv4(words) {
  if (!Array.isArray(words) || words.length !== 8) return "";
  const firstFiveZero = words.slice(0, 5).every((value) => value === 0);
  const compatible = words.slice(0, 6).every((value) => value === 0);
  const mapped = firstFiveZero && words[5] === 0xffff;
  const translated = (
    words.slice(0, 4).every((value) => value === 0)
    && words[4] === 0xffff
    && words[5] === 0
  );
  if (!compatible && !mapped && !translated) return "";
  const hi = words[6];
  const lo = words[7];
  return `${(hi >> 8) & 255}.${hi & 255}.${(lo >> 8) & 255}.${lo & 255}`;
}

function _ipv4Number(raw) {
  const octets = _parseIpv4(raw);
  if (!octets) return null;
  return (
    ((octets[0] << 24) >>> 0)
    + (octets[1] << 16)
    + (octets[2] << 8)
    + octets[3]
  ) >>> 0;
}

function _networkInventory() {
  const rules = [];
  const exact = new Set();
  const interfaces = os.networkInterfaces();
  for (const rows of Object.values(interfaces || {})) {
    for (const row of rows || []) {
      const address = String((row && row.address) || "").split("%")[0].toLowerCase();
      const netmask = String((row && row.netmask) || "").split("%")[0].toLowerCase();
      const family = net.isIP(address);
      if (!family || net.isIP(netmask) !== family) continue;
      exact.add(address);
      if (family === 4) {
        const addressValue = _ipv4Number(address);
        const maskValue = _ipv4Number(netmask);
        if (addressValue !== null && maskValue !== null) {
          rules.push({
            family,
            network: (addressValue & maskValue) >>> 0,
            mask: maskValue,
          });
        }
      } else {
        const addressWords = _ipv6Words(address);
        const maskWords = _ipv6Words(netmask);
        if (addressWords && maskWords) {
          rules.push({
            family,
            network: addressWords.map((word, index) => word & maskWords[index]),
            mask: maskWords,
          });
        }
      }
    }
  }
  return { exact, rules };
}

function _validNetworkInventory(inventory) {
  if (
    !inventory
    || !(inventory.exact instanceof Set)
    || !Array.isArray(inventory.rules)
    || inventory.exact.size === 0
    || inventory.rules.length === 0
  ) return false;
  if ([...inventory.exact].some((address) => !net.isIP(String(address || "")))) {
    return false;
  }
  return inventory.rules.every((rule) => {
    if (!rule || (rule.family !== 4 && rule.family !== 6)) return false;
    if (rule.family === 4) {
      return Number.isInteger(rule.network) && Number.isInteger(rule.mask)
        && rule.network >= 0 && rule.network <= 0xffffffff
        && rule.mask >= 0 && rule.mask <= 0xffffffff;
    }
    return [rule.network, rule.mask].every((words) => (
      Array.isArray(words)
      && words.length === 8
      && words.every((word) => Number.isInteger(word) && word >= 0 && word <= 0xffff)
    ));
  });
}

function _currentNetworkInventory(injected) {
  if (injected !== undefined) {
    return _validNetworkInventory(injected) ? injected : null;
  }
  try {
    const inventory = _networkInventory();
    return _validNetworkInventory(inventory) ? inventory : null;
  } catch (_) {
    return null;
  }
}

function isOnLocalNetwork(raw, inventory) {
  const activeInventory = _currentNetworkInventory(inventory);
  if (!activeInventory) {
    // Interface discovery is part of the guard. If it is unavailable, fail
    // closed instead of assuming a globally scoped on-link address is remote.
    return true;
  }
  const address = String(raw || "").trim().toLowerCase().split("%")[0];
  if (activeInventory.exact.has(address)) return true;
  const family = net.isIP(address);
  if (family === 4) {
    const value = _ipv4Number(address);
    if (value === null) return true;
    return activeInventory.rules.some((rule) => (
      rule.family === 4 && ((value & rule.mask) >>> 0) === (rule.network >>> 0)
    ));
  }
  if (family === 6) {
    const words = _ipv6Words(address);
    if (!words) return true;
    return activeInventory.rules.some((rule) => (
      rule.family === 6
      && words.every((word, index) => (
        (word & rule.mask[index]) === rule.network[index]
      ))
    ));
  }
  return true;
}

function isPublicAddress(raw) {
  const address = String(raw || "").trim().toLowerCase().split("%")[0];
  const family = net.isIP(address);
  if (family === 4) {
    const octets = _parseIpv4(address);
    if (!octets) return false;
    const [a, b, c] = octets;
    if (a === 0 || a === 10 || a === 127) return false;
    if (a === 100 && b >= 64 && b <= 127) return false; // RFC 6598 CGNAT
    if (a === 169 && b === 254) return false;
    if (a === 172 && b >= 16 && b <= 31) return false;
    if (a === 192 && b === 168) return false;
    if (a === 192 && b === 0 && c === 0) return false;
    if (a === 192 && b === 0 && c === 2) return false;
    if (a === 192 && b === 88 && c === 99) return false;
    if (a === 198 && (b === 18 || b === 19)) return false;
    if (a === 198 && b === 51 && c === 100) return false;
    if (a === 203 && b === 0 && c === 113) return false;
    if (a >= 224) return false; // multicast, reserved, broadcast
    return true;
  }
  if (family !== 6) return false;

  const words = _ipv6Words(address);
  if (!words) return false;
  const embedded = _embeddedIpv4(words);
  if (embedded) return isPublicAddress(embedded);
  if ((words[0] & 0xfe00) === 0xfc00) return false; // ULA
  if ((words[0] & 0xffc0) === 0xfe80) return false; // link-local
  if ((words[0] & 0xffc0) === 0xfec0) return false; // deprecated site-local
  if ((words[0] & 0xff00) === 0xff00) return false; // multicast
  if (words[0] === 0x2001 && words[1] === 0x0db8) return false; // documentation
  if (words[0] === 0x2001 && words[1] === 0) return false; // Teredo
  if (words[0] === 0x2002) return false; // 6to4 can embed private IPv4
  if (words[0] === 0x0064 && words[1] === 0xff9b) return false; // NAT64
  return true;
}

function isAllowedWebPort(rawPort) {
  const port = Number(rawPort);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return false;
  if (port === 80 || port === 443) return true;
  if (port < 1024) return false;
  return !BLOCKED_PUBLIC_PORTS.has(port);
}

function _hostNameAllowed(rawHost) {
  const host = _normaliseHost(rawHost);
  if (!host || host.length > 253 || /[\s\x00-\x1f\x7f/@\\]/.test(host)) return false;
  if (BLOCKED_HOSTS.has(host)) return false;
  if (BLOCKED_HOST_SUFFIXES.some((suffix) => host.endsWith(suffix))) return false;
  return true;
}

function _withTimeout(promise, timeoutMs, message) {
  let timer = null;
  return Promise.race([
    promise,
    new Promise((_resolve, reject) => {
      timer = setTimeout(() => reject(new Error(message || "timeout")), timeoutMs);
      if (timer && typeof timer.unref === "function") timer.unref();
    }),
  ]).finally(() => {
    if (timer) clearTimeout(timer);
  });
}

async function resolvePublicTarget(rawHost, options = {}) {
  const host = _normaliseHost(rawHost);
  if (!_hostNameAllowed(host)) throw new Error("host_blocked");

  const literalFamily = net.isIP(host);
  if (literalFamily) {
    const localNetworks = _currentNetworkInventory(options.localNetworks);
    if (
      !localNetworks
      || !isPublicAddress(host)
      || isOnLocalNetwork(host, localNetworks)
    ) {
      throw new Error("address_blocked");
    }
    return { host, address: host, family: literalFamily };
  }

  const lookup = options.lookup || dns.promises.lookup.bind(dns.promises);
  const timeoutMs = Math.max(100, Number(options.timeoutMs || DEFAULT_DNS_TIMEOUT_MS));
  const rows = await _withTimeout(
    Promise.resolve(lookup(host, { all: true, verbatim: true })),
    timeoutMs,
    "dns_timeout",
  );
  const answers = Array.isArray(rows) ? rows : [rows];
  if (!answers.length) throw new Error("dns_empty");
  // Refresh after DNS, not at module/proxy startup. A VPN, DHCP, or adapter
  // change can make a previously public-looking answer local while the HUD is
  // already running. Interface discovery failure (including an empty result)
  // is a hard deny.
  const localNetworks = _currentNetworkInventory(options.localNetworks);
  if (!localNetworks) throw new Error("local_interface_inventory_unavailable");

  const validated = [];
  for (const row of answers) {
    const address = String((row && row.address) || "").trim();
    const family = Number((row && row.family) || net.isIP(address));
    if (
      (family !== 4 && family !== 6)
      || !isPublicAddress(address)
      || isOnLocalNetwork(address, localNetworks)
    ) {
      // Reject mixed public/private answers too.  Choosing only the public
      // record would make round-robin rebinding probabilistic rather than safe.
      throw new Error("address_blocked");
    }
    validated.push({ address, family });
  }
  return { host, address: validated[0].address, family: validated[0].family };
}

function parseAuthority(rawAuthority, defaultPort = 443) {
  const authority = String(rawAuthority || "").trim();
  if (!authority || /[\s\x00-\x1f\x7f/@\\]/.test(authority)) return null;
  let host = "";
  let portText = "";
  if (authority.startsWith("[")) {
    const close = authority.indexOf("]");
    if (close < 2) return null;
    host = authority.slice(1, close);
    if (authority.length > close + 1) {
      if (authority[close + 1] !== ":") return null;
      portText = authority.slice(close + 2);
    }
  } else {
    const firstColon = authority.indexOf(":");
    const lastColon = authority.lastIndexOf(":");
    if (firstColon !== lastColon) return null; // IPv6 requires brackets.
    const colon = lastColon;
    if (colon > 0) {
      host = authority.slice(0, colon);
      portText = authority.slice(colon + 1);
      if (!portText) return null;
    } else {
      host = authority;
    }
  }
  if (portText && !/^\d{1,5}$/.test(portText)) return null;
  const port = portText ? Number(portText) : Number(defaultPort);
  if (!_hostNameAllowed(host) || !isAllowedWebPort(port)) return null;
  return { host: _normaliseHost(host), port };
}

function _rejectHttp(res, statusCode, reason) {
  try {
    res.writeHead(statusCode, {
      "content-type": "text/plain; charset=utf-8",
      "cache-control": "no-store",
      connection: "close",
    });
    res.end(String(reason || "blocked"));
  } catch (_) {
    try { res.destroy(); } catch (_) {}
  }
}

function _rejectConnect(socket, statusCode, reason) {
  try {
    const label = statusCode === 504 ? "Gateway Timeout" : "Forbidden";
    socket.end(`HTTP/1.1 ${statusCode} ${label}\r\nConnection: close\r\nContent-Type: text/plain\r\n\r\n${String(reason || "blocked")}`);
  } catch (_) {
    try { socket.destroy(); } catch (_) {}
  }
}

async function createSafeProxy(options = {}) {
  const injectedLocalNetworks = options.localNetworks === undefined
    ? undefined
    : _currentNetworkInventory(options.localNetworks);
  const startupLocalNetworks = injectedLocalNetworks === undefined
    ? _currentNetworkInventory()
    : injectedLocalNetworks;
  if (!startupLocalNetworks) {
    throw new Error("local_interface_inventory_unavailable");
  }
  const bindHost = "127.0.0.1";
  const lookup = options.lookup;
  const dnsTimeoutMs = Number(options.dnsTimeoutMs || DEFAULT_DNS_TIMEOUT_MS);
  const connectTimeoutMs = Number(options.connectTimeoutMs || DEFAULT_CONNECT_TIMEOUT_MS);
  const maxHttpBytes = Number(options.maxHttpBytes || DEFAULT_MAX_HTTP_BYTES);
  const maxTunnelBytes = Number(options.maxTunnelBytes || DEFAULT_MAX_TUNNEL_BYTES);
  const tunnelIdleMs = Number(options.tunnelIdleMs || DEFAULT_TUNNEL_IDLE_MS);

  const server = http.createServer({ maxHeaderSize: 16 * 1024 });
  const sockets = new Set();
  const upstreamSockets = new Set();
  let closing = false;
  server.on("connection", (socket) => {
    sockets.add(socket);
    socket.once("close", () => sockets.delete(socket));
  });
  server.headersTimeout = 10_000;
  server.requestTimeout = 30_000;
  server.keepAliveTimeout = 5_000;

  server.on("request", async (req, res) => {
    let clientGone = !!(req.destroyed || res.destroyed || closing);
    let upstream = null;
    const abandon = () => {
      if (!res.writableEnded) clientGone = true;
      if (clientGone && upstream) {
        try { upstream.destroy(); } catch (_) {}
      }
    };
    req.once("aborted", abandon);
    res.once("close", abandon);
    let target;
    try {
      target = new URL(String(req.url || ""));
    } catch (_) {
      _rejectHttp(res, 403, "invalid_target");
      return;
    }
    if (target.protocol !== "http:") {
      _rejectHttp(res, 403, "scheme_blocked");
      return;
    }
    const port = Number(target.port || 80);
    if (!isAllowedWebPort(port)) {
      _rejectHttp(res, 403, "port_blocked");
      return;
    }

    let pinned;
    try {
      pinned = await resolvePublicTarget(target.hostname, {
        lookup,
        timeoutMs: dnsTimeoutMs,
        localNetworks: injectedLocalNetworks,
      });
    } catch (_) {
      _rejectHttp(res, 403, "target_blocked");
      return;
    }
    if (clientGone || req.destroyed || res.destroyed || closing) return;

    const headers = Object.assign({}, req.headers || {});
    delete headers["proxy-authorization"];
    delete headers["proxy-connection"];
    headers.host = target.host;
    upstream = http.request({
      host: pinned.address,
      family: pinned.family,
      port,
      method: String(req.method || "GET"),
      path: `${target.pathname || "/"}${target.search || ""}`,
      headers,
      agent: false,
    });
    upstreamSockets.add(upstream);
    upstream.once("close", () => upstreamSockets.delete(upstream));
    if (clientGone || closing) {
      upstream.destroy();
      return;
    }
    upstream.setTimeout(connectTimeoutMs, () => upstream.destroy(new Error("upstream_timeout")));
    upstream.on("response", (upstreamRes) => {
      try { res.writeHead(Number(upstreamRes.statusCode || 502), upstreamRes.headers); } catch (_) {}
      let seen = 0;
      upstreamRes.on("data", (chunk) => {
        seen += chunk ? chunk.length : 0;
        if (seen > maxHttpBytes) {
          upstreamRes.destroy();
          res.destroy();
        }
      });
      upstreamRes.pipe(res);
    });
    upstream.on("error", () => {
      if (!res.headersSent) _rejectHttp(res, 502, "upstream_failed");
      else try { res.destroy(); } catch (_) {}
    });
    req.on("error", () => upstream.destroy());
    req.pipe(upstream);
  });

  server.on("connect", async (req, clientSocket, head) => {
    let clientGone = !!(clientSocket.destroyed || closing);
    let upstream = null;
    const abandon = () => {
      clientGone = true;
      if (upstream) {
        try { upstream.destroy(); } catch (_) {}
      }
    };
    clientSocket.once("close", abandon);
    clientSocket.once("end", abandon);
    clientSocket.once("error", abandon);
    // CONNECT sockets can remain paused while DNS is pending.  Resume just the
    // client side so an early FIN is observed before any upstream is created.
    clientSocket.resume();
    const authority = parseAuthority(req.url, 443);
    if (!authority) {
      _rejectConnect(clientSocket, 403, "target_blocked");
      return;
    }
    let pinned;
    try {
      pinned = await resolvePublicTarget(authority.host, {
        lookup,
        timeoutMs: dnsTimeoutMs,
        localNetworks: injectedLocalNetworks,
      });
    } catch (_) {
      _rejectConnect(clientSocket, 403, "target_blocked");
      return;
    }
    if (
      clientGone ||
      clientSocket.destroyed ||
      clientSocket.readableEnded ||
      !clientSocket.writable ||
      closing
    ) return;

    let established = false;
    upstream = net.connect({
      host: pinned.address,
      family: pinned.family,
      port: authority.port,
    });
    upstreamSockets.add(upstream);
    upstream.once("close", () => upstreamSockets.delete(upstream));
    if (clientGone || closing) {
      upstream.destroy();
      return;
    }
    upstream.setTimeout(connectTimeoutMs, () => upstream.destroy(new Error("upstream_timeout")));
    upstream.once("connect", () => {
      if (clientGone || clientSocket.destroyed || closing) {
        upstream.destroy();
        return;
      }
      established = true;
      upstream.setTimeout(tunnelIdleMs, () => upstream.destroy());
      clientSocket.setTimeout(tunnelIdleMs, () => clientSocket.destroy());
      try { clientSocket.write("HTTP/1.1 200 Connection Established\r\n\r\n"); } catch (_) {}
      if (head && head.length) upstream.write(head);
      let transferred = 0;
      const account = (chunk) => {
        transferred += chunk ? chunk.length : 0;
        if (transferred > maxTunnelBytes) {
          try { upstream.destroy(); } catch (_) {}
          try { clientSocket.destroy(); } catch (_) {}
        }
      };
      upstream.on("data", account);
      clientSocket.on("data", account);
      upstream.pipe(clientSocket);
      clientSocket.pipe(upstream);
    });
    upstream.once("error", () => {
      if (clientSocket.destroyed) return;
      if (established) clientSocket.destroy();
      else _rejectConnect(clientSocket, 504, "upstream_failed");
    });
  });

  server.on("clientError", (_error, socket) => {
    try { socket.end("HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n"); } catch (_) {}
  });

  await new Promise((resolve, reject) => {
    const onError = (error) => {
      server.removeListener("listening", onListening);
      reject(error);
    };
    const onListening = () => {
      server.removeListener("error", onError);
      resolve();
    };
    server.once("error", onError);
    server.once("listening", onListening);
    server.listen({ host: bindHost, port: 0, exclusive: true });
  });

  const address = server.address();
  const port = address && typeof address === "object" ? Number(address.port) : 0;
  if (!port) {
    try { server.close(); } catch (_) {}
    throw new Error("proxy_bind_failed");
  }

  return {
    host: bindHost,
    port,
    proxyRules: `http=${bindHost}:${port};https=${bindHost}:${port}`,
    close: () => new Promise((resolve) => {
      closing = true;
      try { server.close(() => resolve()); } catch (_) { resolve(); }
      for (const socket of sockets) {
        try { socket.destroy(); } catch (_) {}
      }
      for (const socket of upstreamSockets) {
        try { socket.destroy(); } catch (_) {}
      }
      try {
        if (typeof server.closeAllConnections === "function") server.closeAllConnections();
      } catch (_) {}
    }),
    _server: server,
  };
}

module.exports = {
  createSafeProxy,
  isAllowedWebPort,
  isOnLocalNetwork,
  isPublicAddress,
  parseAuthority,
  resolvePublicTarget,
};
