/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./index.html", "./src/**/*.{jsx,js}"],
  theme: {
    extend: {
      colors: {
        // Paleta UFPel + SIIEPE
        primary: "#003A70",   // azul institucional
        secondary: "#FFBF00", // amarelo institucional
        accentGreen: "#29B673",
        accentPurple: "#7C3AED",
        accentOrange: "#FF7A00",
        bg: "#F7F9FC",
        card: "#FFFFFF",
        ink: "#1F2937",
        inkSoft: "#4B5563"
      },
      borderRadius: {
        xl: "1rem",
        "2xl": "1.25rem"
      },
      fontFamily: {
        sans: ['"Roboto"', '"Open Sans"', "ui-sans-serif", "system-ui", "Segoe UI", "Helvetica", "Arial", "Apple Color Emoji", "Segoe UI Emoji"]
      },
      boxShadow: {
        soft: "0 10px 30px rgba(0,0,0,0.06)"
      },
      keyframes: {
        pulseDots: {
          "0%, 80%, 100%": { opacity: "0.3" },
          "40%": { opacity: "1" }
        }
      },
      animation: {
        pulseDots: "pulseDots 1.4s infinite ease-in-out"
      }
    }
  },
  plugins: [],
};
