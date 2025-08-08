import React from 'react'
export default function MoveList({ moves }:{moves:string[]}){
  const pairs:string[][] = []
  for(let i=0;i<moves.length;i+=2){
    pairs.push([moves[i], moves[i+1] || ''])
  }
  return (
    <div style={{maxHeight: 240, overflow: 'auto', border:'1px solid #e5e7eb', borderRadius:8, padding:8}}>
      {pairs.map((p,idx)=> (
        <div key={idx} style={{display:'grid', gridTemplateColumns:'32px 1fr 1fr', gap:8}}>
          <div style={{opacity:0.6}}>{idx+1}.</div>
          <div>{p[0]}</div>
          <div>{p[1]}</div>
        </div>
      ))}
    </div>
  )
}