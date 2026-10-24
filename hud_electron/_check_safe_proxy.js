"use strict";

const assert = require("assert");
const http = require("http");
const net = require("net");
const fs = require("fs");
const path = require("path");
const os = require("os");
const {
  createSafeProxy,
  isAllowedWebPort,
  isOnLocalNetwork,
  isPublicAddress,
  parseAuthority,
  resolvePublicTarget,
} = require("./syrix-safe-proxy.js");

const TEST_LOCAL_NETWORKS = {
  exact: new Set(["127.0.0.1"]),
  rules: [{ family: 4, network: 0x7f000000, mask: 0xff000000 }],
};

async function main() {
  const mainSource = fs.readFileSync(path.join(__dirname, "main.js"), "utf8");
  assert.match(mainSource, /createSafeProxy/);
  assert.match(mainSource, /proxyBypassRules:\s*"<-loopback>"/);
  assert.match(mainSource, /force-webrtc-ip-handling-policy/);
  assert.match(mainSource, /browser_network_guard_unavailable/);
  assert.match(mainSource, /supercomputer_ephemeral/);
  assert.match(mainSource, /_internals\.partitionId/);
  const preloadSource = fs.readFileSync(path.join(__dirname, "preload.js"), "utf8");
  assert.match(preloadSource, /function _scInvoke/);
  for (const value of [
    "0.0.0.0", "10.1.2.3", "100.64.0.1", "100.127.255.254",
    "127.0.0.1", "169.254.169.254", "172.16.0.1", "192.168.1.1",
    "198.18.0.1", "224.0.0.1", "::", "::1", "fc00::1", "fd12::1",
    "fe80::1", "fec0::1", "ff02::1", "::ffff:7f00:1", "::ffff:0:7f00:1",
    "::7f00:1", "2002:7f00:1::",
    "64:ff9b::7f00:1",
  ]) assert.strictEqual(isPublicAddress(value), false, `must block ${value}`);
  for (const value of ["1.1.1.1", "8.8.8.8", "93.184.216.34", "2606:4700:4700::1111"]) {
    assert.strictEqual(isPublicAddress(value), true, `must allow ${value}`);
  }
  assert.strictEqual(isAllowedWebPort(80), true);
  assert.strictEqual(isAllowedWebPort(443), true);
  assert.strictEqual(isAllowedWebPort(8080), true);
  assert.strictEqual(isAllowedWebPort(22), false);
  assert.strictEqual(isAllowedWebPort(3389), false);
  assert.deepStrictEqual(parseAuthority("example.test:443"), { host: "example.test", port: 443 });
  assert.deepStrictEqual(parseAuthority("127.0.0.1:443"), { host: "127.0.0.1", port: 443 });
  for (const authority of [
    "example.test:", "example.test:4.43e2", "example.test:0x1bb",
    "2001:4860:4860::8888:443", "[2001:4860:4860::8888]:4.43e2",
  ]) assert.strictEqual(parseAuthority(authority), null, `must reject ${authority}`);

  let sawInterface = false;
  for (const rows of Object.values(os.networkInterfaces() || {})) {
    for (const row of rows || []) {
      if (!row || !row.address) continue;
      sawInterface = true;
      assert.strictEqual(isOnLocalNetwork(row.address), true, `must block local ${row.address}`);
    }
  }
  assert.strictEqual(sawInterface, true, "expected at least one local interface");
  await assert.rejects(
    createSafeProxy({ localNetworks: { exact: new Set(), rules: [] } }),
    /local_interface_inventory_unavailable/,
    "truthy but empty interface inventory must fail startup",
  );
  await assert.rejects(
    resolvePublicTarget("rebind.test", {
      lookup: async () => [
        { address: "93.184.216.34", family: 4 },
        { address: "127.0.0.1", family: 4 },
      ],
    }),
    /address_blocked/,
  );

  // Adapter/VPN state can change after proxy startup. The next resolution must
  // use the new on-link inventory rather than the module/startup snapshot.
  const originalNetworkInterfaces = os.networkInterfaces;
  const changedNetworkProxy = await createSafeProxy({
    lookup: async () => [{ address: "93.184.216.34", family: 4 }],
  });
  try {
    os.networkInterfaces = () => ({
      changed: [{
        address: "93.184.216.10",
        netmask: "255.255.255.0",
        family: "IPv4",
      }],
    });
    const response = await new Promise((resolve, reject) => {
      const req = http.request({
        host: changedNetworkProxy.host,
        port: changedNetworkProxy.port,
        method: "GET",
        path: "http://network-change.test/secret",
      }, (res) => {
        const chunks = [];
        res.on("data", (chunk) => chunks.push(chunk));
        res.on("end", () => resolve({
          status: res.statusCode,
          body: Buffer.concat(chunks).toString("utf8"),
        }));
      });
      req.once("error", reject);
      req.end();
    });
    assert.strictEqual(response.status, 403);
    assert.match(response.body, /target_blocked/);
  } finally {
    os.networkInterfaces = originalNetworkInterfaces;
    await changedNetworkProxy.close();
  }

  const blockedLookup = async (host) => {
    if (host === "public.test") return [{ address: "93.184.216.34", family: 4 }];
    return [{ address: "127.0.0.1", family: 4 }];
  };
  const proxy = await createSafeProxy({ lookup: blockedLookup, connectTimeoutMs: 100 });
  try {
    const response = await new Promise((resolve, reject) => {
      const req = http.request({
        host: proxy.host,
        port: proxy.port,
        method: "GET",
        path: "http://private.test/secret",
      }, (res) => {
        const chunks = [];
        res.on("data", (chunk) => chunks.push(chunk));
        res.on("end", () => resolve({ status: res.statusCode, body: Buffer.concat(chunks).toString("utf8") }));
      });
      req.once("error", reject);
      req.end();
    });
    assert.strictEqual(response.status, 403);
    assert.match(response.body, /target_blocked/);

    const connectStatus = await new Promise((resolve, reject) => {
      const socket = net.connect({ host: proxy.host, port: proxy.port });
      socket.setEncoding("utf8");
      socket.once("connect", () => socket.write("CONNECT private.test:443 HTTP/1.1\r\nHost: private.test:443\r\n\r\n"));
      socket.once("data", (chunk) => { resolve(chunk); socket.destroy(); });
      socket.once("error", reject);
    });
    assert.match(connectStatus, /^HTTP\/1\.1 403/);
  } finally {
    await proxy.close();
  }

  // A client that disappears while DNS is pending must never create an
  // upstream socket after the lookup resolves.
  let lateUpstreamConnects = 0;
  const originalConnect = net.connect;
  const delayed = await createSafeProxy({
    lookup: async () => {
      await new Promise((resolve) => setTimeout(resolve, 80));
      return [{ address: "93.184.216.34", family: 4 }];
    },
    localNetworks: TEST_LOCAL_NETWORKS,
  });
  try {
    net.connect = function patchedConnect(options, ...args) {
      if (options && options.host === "93.184.216.34") {
        lateUpstreamConnects += 1;
        const fake = new net.Socket();
        setImmediate(() => fake.destroy());
        return fake;
      }
      return originalConnect.call(net, options, ...args);
    };
    const client = new net.Socket();
    await new Promise((resolve, reject) => {
      client.once("connect", resolve);
      client.once("error", reject);
      client.connect({ host: delayed.host, port: delayed.port });
    });
    client.write("CONNECT delayed.test:443 HTTP/1.1\r\nHost: delayed.test:443\r\n\r\n");
    setTimeout(() => client.destroy(), 5);
    await new Promise((resolve) => setTimeout(resolve, 160));
    assert.strictEqual(lateUpstreamConnects, 0);
  } finally {
    net.connect = originalConnect;
    await delayed.close();
  }

  console.log("safe proxy checks passed");
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
