import { Button } from "@/components/ui/button";

const Index = () => {
  return (
    <main className="min-h-screen flex items-center justify-center bg-background">
      <section className="text-center max-w-2xl px-6 py-12">
        <h1 className="text-4xl md:text-5xl font-bold tracking-tight mb-4">
          Clipper — Medal-like PC Clipping Tool
        </h1>
        <p className="text-lg md:text-xl text-muted-foreground mb-8">
          Continuously record your primary monitor in the background with FFmpeg and save the last 2 minutes anytime with F4/F5.
        </p>
        <div className="flex items-center justify-center gap-4">
          <a href="#" onClick={(e) => { e.preventDefault(); window.open('README.md', '_blank'); }}>
            <Button variant="default">Read Setup Guide</Button>
          </a>
          <a href="#" onClick={(e) => { e.preventDefault(); window.open('clipper.py', '_blank'); }}>
            <Button variant="secondary">View Python Script</Button>
          </a>
        </div>
      </section>
    </main>
  );
};

export default Index;
