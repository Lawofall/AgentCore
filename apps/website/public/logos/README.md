# 模型厂商 logo

首页「主流大模型，都能接」那面墙的标识资源。

## 怎么加

1. 把厂商官方 SVG 放到本目录，文件名用 `src/content/home.ts` 里 `MARQUEE.vendors` 的 `slug`：

   ```
   deepseek.svg  openai.svg  claude.svg  gemini.svg  qwen.svg
   kimi.svg      zhipu.svg   minimax.svg grok.svg    mimo.svg
   openrouter.svg
   ```

2. 把 `home.ts` 里对应那一项加上 `hasLogo: true`：

   ```ts
   { name: "OpenAI", slug: "openai", hasLogo: true },
   ```

没加 `hasLogo` 的厂商会降级成字标，墙照常成立（参考站那面墙本来也有一半是字标）。

## 要求

- **SVG**，`viewBox` 完整、不要带外边距。墙上按定高 `2.75rem` 等比缩放，最大宽 `11rem`。
- **单色或深色版本**优先。墙上默认 `grayscale(1) + opacity .55`，hover 才还原本色——彩色原图也能用，但深色单色版效果最稳。
- 用厂商**官方**下载的标识，不要自己描摹或改造：这是别人的商标。

## 授权提醒

展示第三方厂商标识来说明「已支持接入」属于常见的指代性使用，但各家品牌规范对改色、变形、加边框的限制不同（例如是否允许单色化）。上线前请对一遍各厂商的 brand guidelines——本目录只负责放文件，不代表已完成合规确认。
