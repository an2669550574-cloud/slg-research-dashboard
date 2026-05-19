/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: ['selector', ':root:not([data-theme="light"])'],
  theme: {
    extend: {
      colors: {
        // 电光 azure：比原 #3b82f6 更亮更有"信号感"，与 --accent 同源
        brand: {
          50: '#eef5ff',
          500: '#4696ff',
          600: '#2f7ff0',
          700: '#1f63cf',
          900: '#16306b',
        },
        accent: 'rgb(var(--accent) / <alpha-value>)',
        signal: 'rgb(var(--signal) / <alpha-value>)',
      },
      boxShadow: {
        card: '0 1px 0 0 rgb(255 255 255 / 0.03) inset, 0 8px 24px -12px rgb(0 0 0 / 0.6)',
        pop: '0 16px 40px -12px rgb(0 0 0 / 0.7)',
      },
      // 主题感知 token：在亮/暗主题下颜色不同（由 :root 与 :root[data-theme="light"] 中的 CSS var 切换）
      // 分通道配置避免 text-text-primary 这种丑陋类名
      backgroundColor: ({ theme }) => ({
        ...theme('colors'),
        base: 'rgb(var(--bg-base) / <alpha-value>)',
        surface: 'rgb(var(--bg-surface) / <alpha-value>)',
        elevated: 'rgb(var(--bg-elevated) / <alpha-value>)',
      }),
      textColor: ({ theme }) => ({
        ...theme('colors'),
        primary: 'rgb(var(--text-primary) / <alpha-value>)',
        secondary: 'rgb(var(--text-secondary) / <alpha-value>)',
        muted: 'rgb(var(--text-muted) / <alpha-value>)',
      }),
      borderColor: ({ theme }) => ({
        ...theme('colors'),
        default: 'rgb(var(--border-default) / <alpha-value>)',
        strong: 'rgb(var(--border-strong) / <alpha-value>)',
      }),
      divideColor: ({ theme }) => ({
        ...theme('colors'),
        default: 'rgb(var(--border-default) / <alpha-value>)',
      }),
      placeholderColor: ({ theme }) => ({
        ...theme('colors'),
        muted: 'rgb(var(--text-muted) / <alpha-value>)',
      }),
    }
  },
  plugins: []
}
