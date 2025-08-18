import React from "react";
import Chat from "./components/Chat.jsx";

export default function App() {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="bg-primary text-white">
        <div className="max-w-5xl mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <svg width="28" height="28" viewBox="0 0 24 24" className="text-secondary">
              <circle cx="12" cy="12" r="10" fill="currentColor" />
            </svg>
            <h1 className="text-xl font-bold">SIIEPE • Chat UFPel</h1>
          </div>
          <div className="text-sm opacity-90">
            Plataforma de perguntas com documentos da SIIEPE
          </div>
        </div>
      </header>

      <main className="flex-1">
        <Chat />
      </main>

      <footer className="text-center text-inkSoft text-sm py-6">
        © {new Date().getFullYear()} UFPel — Projeto acadêmico (SIIEPE)
      </footer>
    </div>
  );
}
