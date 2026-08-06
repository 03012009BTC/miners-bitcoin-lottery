// Validate the dashboard's own JS hasher against the known block 125552.
// Extracts the <script> block from dashboard.html and runs it with tiny DOM stubs.
const fs = require("fs");

const path = require("path");
const html = fs.readFileSync(path.join(__dirname, "dashboard.html"), "utf8");
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error("no <script> block found"); process.exit(1); }
const code = m[1];

const fakeEl = new Proxy({}, {
  get: (t, k) => {
    if (k === "style") return {};
    if (k === "addEventListener" || k === "insertAdjacentHTML" || k === "remove" ||
        k === "setAttribute" || k === "querySelector" || k === "appendChild" || k === "select")
      return () => fakeEl;
    if (k === "children") return [];
    if (k === "getBoundingClientRect") return () => ({left:0, top:0, width:1000, height:200});
    return "";
  },
  set: () => true,
});
const stubs = {
  document: {
    getElementById: () => fakeEl, createElement: () => fakeEl,
    body: fakeEl, addEventListener: () => {}, execCommand: () => false,
  },
  navigator: {userAgent: "node-test", clipboard: {writeText: async () => {}}},
  location: {protocol: "http:", host: "localhost:8888"},
  performance: {now: () => Date.now()},
  fetch: async () => { throw new Error("no network in test"); },
  setInterval: () => 0,
  setTimeout: () => 0,
  console,
};

const runner = new Function(...Object.keys(stubs), code + `
  ;return {loadJob, hashBatch, PLAY};
`);
const api = runner(...Object.values(stubs));

const hdr = "0100000081cd02ab7e569e8bcd9317e2fe99f2de44d49ab2b8851ba4a308000000000000"
          + "e320b6c2fffc8d750423db8b1eb942ae710e951ed797f7affc8892b0f1fc122bc7f5d74df2b9441a";
const target = "00000000ffff0000000000000000000000000000000000000000000000000000";
const known = 2504433986;

// 1) does it recognise the historical winning nonce as a valid share?
api.loadJob({uid: 1, header: hdr, target: target, start: known, count: 2});
const found = api.hashBatch(1);
const h2 = api.PLAY.h2;
let hex = "";
for (let i = 7; i >= 0; i--) {
  const v = h2[i];
  // block hash = digest reversed, so emit each word's bytes back to front
  hex += (v & 0xff).toString(16).padStart(2,"0") + ((v>>>8)&0xff).toString(16).padStart(2,"0")
       + ((v>>>16)&0xff).toString(16).padStart(2,"0") + ((v>>>24)&0xff).toString(16).padStart(2,"0");
}
console.log("known nonce recognised as share :", found === known);
console.log("hash (reversed = block hash)    :", hex);
console.log("matches historical block hash   :",
  hex === "00000000000000001e8d6829a8a21adc5d38d0a473b144b6765798e61f98bd1d");

// 2) does it correctly reject a wrong nonce?
api.loadJob({uid: 1, header: hdr, target: target, start: 12345, count: 10});
console.log("wrong nonces rejected           :", api.hashBatch(10) === -1);

// 3) speed
api.loadJob({uid: 1, header: hdr, target: target, start: 0, count: 1 << 22});
const t0 = Date.now();
api.hashBatch(200000);
const dt = (Date.now() - t0) / 1000;
console.log("speed                           :", Math.round(200000/dt/1000), "kH/s (this machine, Node)");
