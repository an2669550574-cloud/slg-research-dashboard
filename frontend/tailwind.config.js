/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: ['selector', ':root:not([data-theme="light"])'],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#eff6ff',
          500: '#3b82f6',
          600: '#2563eb',
          700: '#1d4ed8',
          900: '#1e3a8a',
        },
        // 主题感知 token：在亮/暗主题下颜色不同（由 :root 与 :root[data-theme="light"] 中的 CSS var 切换）
        surface: 'rgb(var(--bg-surface) / <alpha-value>)',
        base: 'rgb(var(--bg-base) / <alpha-value>)',
        elevated: 'rgb(var(--bg-elevated) / <alpha-value>)',
        'border-default': 'rgb(var(--border-default) / <alpha-value>)',
      }
    }
  },
  plugins: []
}
