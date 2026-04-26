/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        playto: {
          50: '#f0f4ff',
          500: '#4f6ef7',
          600: '#3b5bf0',
          700: '#2d4de0',
        },
      },
    },
  },
  plugins: [],
};
