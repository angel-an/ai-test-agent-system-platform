import os from 'os';

/**
 * 自动探测本机局域网 IPv4 地址。
 * 优先返回 192.168.x.x / 10.x.x.x / 172.16-31.x.x 网段的地址，
 * 找不到时回退到 127.0.0.1。
 */
function getLanIP() {
  const interfaces = os.networkInterfaces();
  for (const name of Object.keys(interfaces)) {
    for (const iface of interfaces[name]) {
      if (
        iface.family === 'IPv4' &&
        !iface.internal &&
        (iface.address.startsWith('192.168.') ||
          iface.address.startsWith('10.') ||
          /^172\.(1[6-9]|2\d|3[01])\./.test(iface.address))
      ) {
        return iface.address;
      }
    }
  }
  return '127.0.0.1';
}

const LAN_IP = getLanIP();
// FIX-2026-10-19：`next start` 不会重算 rewrites —— 它读的是 `next build` 时写进
// `.next/routes-manifest.json` 的 destination。原先这里用探测到的局域网 IP，导致
// 机器 IP 一变（实测 192.168.84.189 → 192.168.81.159）生产前端仍打旧 IP，
// `/lg/*` 全部 ETIMEDOUT 后 500，而控制台打印的却是新 IP，极难判读。
// 改为默认走本机回环（LangGraph 绑 0.0.0.0:2026，回环必然可达），与网卡解耦；
// 需要指向其他机器时用环境变量 LANGGRAPH_PROXY_TARGET 覆盖（构建期生效）。
const LG_TARGET = process.env.LANGGRAPH_PROXY_TARGET || 'http://127.0.0.1:2026';
console.log(
  `[next.config] LangGraph proxy → ${LG_TARGET}` +
    (process.env.LANGGRAPH_PROXY_TARGET ? ' (env)' : ` (loopback; 探测到的 LAN IP ${LAN_IP})`),
);

/** @type {import('next').NextConfig} */
const nextConfig = {
  // FIX-2026-09-28：开发/生产构建目录隔离。
  // 背景：`next dev` 与 `next start` 同时运行会写同一个 `.next`，实测导致
  // `.next/BUILD_ID` 消失、`next start` 报“找不到生产构建”，:3000 上究竟是
  // dev 还是生产产物无法分辨。这里让 dev 独占 `.next-dev`，生产构建仍用 `.next`。
  distDir: process.env.NODE_ENV === 'development' ? '.next-dev' : '.next',
  // 禁用 React Strict Mode，避免开发环境下组件双重挂载导致流请求被取消
  reactStrictMode: false,
  // 禁用 webpack 持久化缓存，避免 Windows 下缓存文件竞争导致的 ENOENT 错误
  webpack: (config, { dev }) => {
    if (dev) {
      config.cache = {
        type: 'memory',
      };
    }
    return config;
  },
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: 'http://127.0.0.1:8000/api/:path*',
      },
      {
        // FIX-2026-09-18: 127.0.0.1:2026 会被 deep-agents-harness 的 langgraph dev 抢占
        // （只注册 research_agent），平台 LangGraph 必须独占 2026。
        // FIX-2026-09-20: 改为启动时自动探测局域网 IP，避免 IP 变化后需手动修改。
        // FIX-2026-10-19: 探测 IP 方案对 `next start` 无效（rewrites 在构建期固化），
        // IP 一变就断链。改为默认回环 + 环境变量覆盖，见上方 LG_TARGET。
        source: '/lg/:path*',
        destination: `${LG_TARGET}/:path*`,
      },
    ];
  },
};

export default nextConfig;
