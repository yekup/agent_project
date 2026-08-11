/** @type {import('tailwindcss').Config} */
import typography from '@tailwindcss/typography'

export default {
  content: ['./index.html', './src/**/*.{vue,js}'],
  theme: {
    extend: {
      colors: {
        ink: {
          DEFAULT: '#37352f', // 正文
          soft: '#787774',    // 次级文字
          faint: '#9b9a97',   // 弱化文字
        },
        paper: '#f7f6f3',     // 页面底色（米白）
        card: '#ffffff',
        line: '#e7e5e0',      // 边框
        accent: {
          DEFAULT: '#2f6f4f', // 强调色（墨绿）
          soft: '#eef4f0',    // 强调色浅底
          hover: '#265d41',
        },
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'PingFang SC',
               'Hiragino Sans GB', 'Microsoft YaHei', 'sans-serif'],
      },
    },
  },
  plugins: [typography],
}
