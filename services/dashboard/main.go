package main

import (
	"log"
	"net/http"
	"os"
)

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "3000"
	}

	http.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("OK"))
	})

	fs := http.FileServer(http.Dir("./static"))
	http.Handle("/", fs)

	log.Printf("[Dashboard] Live Visual Web Dashboard serving on :%s", port)
	if err := http.ListenAndServe(":"+port, nil); err != nil {
		log.Fatalf("[Dashboard] Server error: %v", err)
	}
}

