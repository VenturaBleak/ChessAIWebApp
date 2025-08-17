use chess::{Board, ChessMove, MoveGen, Square};
use engine::search::{Search, root_search, pv_line_from_tt, current_best_or_default};
use engine::types::*;
use std::io::{self, BufRead, Write};
use std::str::FromStr;
use std::sync::{Arc};
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

fn parse_uci_move(s: &str) -> Option<ChessMove> {
    if s.len() < 4 { return None; }
    let from = Square::from_str(&s[0..2]).ok()?;
    let to = Square::from_str(&s[2..4]).ok()?;
    let promo = if s.len() == 5 {
        match &s[4..5] {
            "q" => Some(chess::Piece::Queen),
            "r" => Some(chess::Piece::Rook),
            "b" => Some(chess::Piece::Bishop),
            "n" => Some(chess::Piece::Knight),
            _ => None,
        }
    } else { None };
    Some(ChessMove::new(from, to, promo))
}

fn main() -> io::Result<()> {
    let stdin = io::stdin();
    let mut stdout = io::stdout();

    println!("id name PyRefEngine (AB-only)");
    println!("id author open-source");
    println!("uciok");
    stdout.flush()?;

    let (tx_cmd, rx_cmd) = std::sync::mpsc::channel::<String>();
    thread::spawn(move || {
        for line in stdin.lock().lines() {
            if let Ok(s) = line {
                let cmd = s.trim().to_string();
                if tx_cmd.send(cmd).is_err() { break; }
            } else { break; }
        }
    });

    let mut board = Board::default();
    let mut search_handle: Option<std::thread::JoinHandle<()>> = None;
    let stop_flag = Arc::new(AtomicBool::new(false));
    let bestmove_sent = Arc::new(AtomicBool::new(false));

    while let Ok(cmd) = rx_cmd.recv() {
        println!("info string dbg=recv '{}'", cmd);
        stdout.flush()?;

        if cmd == "uci" {
            println!("id name PyRefEngine (AB-only)");
            println!("id author open-source");
            println!("uciok");
            stdout.flush()?;
            continue;
        }
        if cmd == "isready" {
            println!("readyok");
            stdout.flush()?;
            continue;
        }
        if cmd.starts_with("ucinewgame") {
            if let Some(h) = search_handle.take() {
                stop_flag.store(true, Ordering::Relaxed);
                let _ = h.join();
                stop_flag.store(false, Ordering::Relaxed);
                bestmove_sent.store(false, Ordering::Relaxed);
            }
            board = Board::default();
            stdout.flush()?;
            continue;
        }
        if cmd.starts_with("position ") {
            if let Some(after) = cmd.strip_prefix("position ") {
                let parts: Vec<&str> = after.split_whitespace().collect();
                let mut idx = 0;
                if parts.get(0) == Some(&"startpos") {
                    board = Board::default();
                    idx = 1;
                } else if parts.get(0) == Some(&"fen") {
                    if parts.len() >= 7 {
                        let fen = parts[1..7].join(" ");
                        match Board::from_str(&fen) {
                            Ok(b) => board = b,
                            Err(e) => {
                                println!("info string dbg=position-parse-error {}:{}", "FEN", e);
                                board = Board::default();
                                stdout.flush()?;
                                continue;
                            }
                        }
                        idx = 7;
                    } else {
                        println!("info string dbg=position-parse-error {}:{}", "FEN", "expected 6 tokens");
                        board = Board::default();
                        stdout.flush()?;
                        continue;
                    }
                } else {
                    println!("info string dbg=position-parse-error {}:{}", "SYNTAX", "expected startpos or fen");
                    board = Board::default();
                    stdout.flush()?;
                    continue;
                }

                if idx < parts.len() && parts[idx] == "moves" {
                    for mv_str in &parts[idx + 1..] {
                        if let Some(mv) = parse_uci_move(mv_str) {
                            if MoveGen::new_legal(&board).any(|m| m == mv) {
                                board = board.make_move_new(mv);
                            } else {
                                println!("info string dbg=bad-move {}", mv_str);
                            }
                        } else {
                            println!("info string dbg=bad-move {}", mv_str);
                        }
                    }
                }
            }
            stdout.flush()?;
            continue;
        }

        if cmd.starts_with("go ") {
            let mut depth: i32 = DEFAULT_DEPTH;
            let mut rollouts: i32 = DEFAULT_ROLLOUTS;
            let mut movetime_ms: Option<u64> = None;

            let parts: Vec<&str> = cmd.split_whitespace().collect();
            let mut i = 1;
            while i + 1 < parts.len() {
                match parts[i] {
                    "depth" => { if let Ok(d) = parts[i + 1].parse::<i32>() { depth = d; } i += 2; }
                    "rollouts" => { if let Ok(r) = parts[i + 1].parse::<i32>() { rollouts = r; } i += 2; }
                    "movetime" => { if let Ok(ms) = parts[i + 1].parse::<u64>() { movetime_ms = Some(ms); } i += 2; }
                    _ => i += 1,
                }
            }

            println!("info string dbg=go depth={} rollouts={} (rollouts ignored; AB-only)", depth, rollouts);
            stdout.flush()?;

            if let Some(h) = search_handle.take() {
                stop_flag.store(true, Ordering::Relaxed);
                let _ = h.join();
                stop_flag.store(false, Ordering::Relaxed);
                bestmove_sent.store(false, Ordering::Relaxed);
            }

            let b0 = board;
            let stop = Arc::clone(&stop_flag);
            let sent = Arc::clone(&bestmove_sent);

            search_handle = Some(thread::spawn(move || {
                let start = Instant::now();
                let time_limit = movetime_ms.map(Duration::from_millis);

                let mut search = Search::new(Arc::clone(&stop));

                let mut last_score = search.evaluate(&b0);
                let mut root_best: Option<ChessMove> = None;

                let max_depth = depth.max(1).min(MAX_AB_DEPTH);
                for d in 1..=max_depth {
                    if let Some(tl) = time_limit { if start.elapsed() >= tl { break; } }
                    if stop.load(Ordering::Relaxed) { break; }

                    search.on_new_iter();

                    let mut window = ASP_WINDOW;
                    let mut alpha = last_score - window;
                    let mut beta  = last_score + window;

                    let mut score;
                    loop {
                        let (best_move, sc) = root_search(&mut search, &b0, d, alpha, beta);
                        score = sc;
                        if (score <= alpha || score >= beta) && window < ASP_MAX_WIDEN {
                            window = (window * 2).min(ASP_MAX_WIDEN);
                            alpha = score - window;
                            beta  = score + window;
                            continue;
                        } else {
                            if let Some(m) = best_move { root_best = Some(m); }
                            break;
                        }
                    }

                    last_score = clamp(score, -INF + 1, INF - 1);

                    let elapsed = start.elapsed().as_secs_f64().max(1e-6);
                    let nps = (search.nodes as f64 / elapsed) as u64;
                    let pv = pv_line_from_tt(b0, &search.tt, d as usize);
                    let pv_str = pv.iter().map(|m| m.to_string()).collect::<Vec<_>>().join(" ");
                    println!("info depth {} nodes {} nps {} score cp {} pv {}", d, search.nodes, nps, last_score, pv_str);
                    println!("info string dbg=iter depth={}", d);
                    io::stdout().flush().ok();

                    if stop.load(Ordering::Relaxed) { break; }
                    if let Some(tl) = time_limit { if start.elapsed() >= tl { break; } }
                }

                if stop.load(Ordering::Relaxed) { return; }

                if !sent.swap(true, Ordering::Relaxed) {
                    let best_uci = if let Some(m) = root_best { m.to_string() } else { current_best_or_default(&b0) };
                    println!("bestmove {}", best_uci);
                    io::stdout().flush().ok();
                }
            }));

            continue;
        }

        if cmd == "stop" {
            stop_flag.store(true, Ordering::Relaxed);
            if !bestmove_sent.swap(true, Ordering::Relaxed) {
                println!("bestmove {}", current_best_or_default(&board));
                stdout.flush()?;
            }
            continue;
        }

        if cmd == "quit" {
            println!("info string dbg=quit");
            stdout.flush()?;
            if let Some(h) = search_handle.take() {
                stop_flag.store(true, Ordering::Relaxed);
                let _ = h.join();
            }
            break;
        }
    }

    Ok(())
}
