import type { Metadata } from "next";
import "@/app/globals.css";

export const metadata: Metadata = {
  title: "Looplense — Teacher",
  description: "Looplense teacher dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
