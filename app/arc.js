/* ---------------------------------------------------------------- arc.js
   The atmosphere both pages sit on: a drifting starfield, and a filament
   torus drawn as a bundle of nested elliptical arcs seen edge-on, composited
   additively so the overlaps bloom. Colour runs along the console's own
   categorical ramp - accent blue through series violet - so nothing here
   introduces a hue the palette does not already contain.

   Inlined into both pages by build_app.py, so the two cannot drift apart.

   Every canvas carrying data-arc is initialised. data-arc picks the figure:

     hero   the cover's opening arc, large and centred, scrolls away
     page   the fixed backdrop behind everything else: stars, and a low
            horizon arc well clear of the reading column
     stars  the same backdrop without the arc, for the cover, whose hero
            already carries one - two arcs in a frame fight each other

   Intensity is theme-controlled through --arc-alpha and --star-alpha rather
   than hard-coded here, so the light theme dims the atmosphere instead of
   needing a second implementation. Pages dispatch "themechange" on window
   after a toggle; we re-read the tokens then.                            */
(function(){
  var nodes=[].slice.call(document.querySelectorAll("canvas[data-arc]"));
  if(!nodes.length) return;
  var reduce=window.matchMedia&&window.matchMedia("(prefers-reduced-motion:reduce)").matches;
  var A={arc:1,star:1};

  function readTokens(){
    var cs=getComputedStyle(document.documentElement);
    var a=parseFloat(cs.getPropertyValue("--arc-alpha")),
        s=parseFloat(cs.getPropertyValue("--star-alpha"));
    A.arc =isNaN(a)?1:a;
    A.star=isNaN(s)?1:s;
  }
  readTokens();

  /* #5E9BD6 -> #9E7ECB -> #C9BCE8 */
  function ramp(k){
    var a=[94,155,214], b=[158,126,203], d=[201,188,232], t;
    if(k<0.62){ t=k/0.62;
      return [(a[0]+(b[0]-a[0])*t)|0,(a[1]+(b[1]-a[1])*t)|0,(a[2]+(b[2]-a[2])*t)|0]; }
    t=(k-0.62)/0.38;
    return [(b[0]+(d[0]-b[0])*t)|0,(b[1]+(d[1]-b[1])*t)|0,(b[2]+(d[2]-b[2])*t)|0];
  }

  function make(c){
    var x=c.getContext("2d"); if(!x) return null;
    var mode=c.getAttribute("data-arc");
    var hero=mode==="hero", starsOnly=mode==="stars";
    var W=0,H=0,DPR=1,stars=[];

    function resize(){
      var r=c.getBoundingClientRect();
      DPR=Math.min(window.devicePixelRatio||1,2);
      W=Math.max(1,r.width); H=Math.max(1,r.height);
      c.width=Math.round(W*DPR); c.height=Math.round(H*DPR);
      x.setTransform(DPR,0,0,DPR,0,0);
      stars=[];
      var n=Math.round(W*H/(hero?9000:11000));
      for(var i=0;i<n;i++) stars.push({
        x:Math.random()*W, y:Math.random()*H,
        r:Math.random()*0.9+0.25, a:Math.random()*0.5+0.12,
        p:Math.random()*Math.PI*2, s:Math.random()*0.5+0.2});
    }

    function draw(ms){
      var t=ms*0.001;
      x.clearRect(0,0,W,H);

      if(A.star>0.01){
        for(var i=0;i<stars.length;i++){
          var s=stars[i];
          x.globalAlpha=s.a*A.star*(reduce?1:(0.6+0.4*Math.sin(t*s.s+s.p)));
          x.fillStyle="#C8D4E6";
          x.beginPath(); x.arc(s.x,s.y,s.r,0,6.2832); x.fill();
        }
        x.globalAlpha=1;
      }
      if(starsOnly||A.arc<=0.01) return;

      /* The page arc is a horizon: wide, shallow, and pushed below the
         viewport so only its crown enters the frame, well clear of the
         column the tables are read in. */
      var cx=W/2, cy, RX, RY;
      if(hero){ cy=H*0.46;  RX=Math.min(W*0.335,430); RY=RX*0.46; }
      else    { cy=H*1.22;  RX=W*0.60;                RY=Math.min(RX*0.28,H*0.36); }

      var N=hero?104:78;
      x.globalCompositeOperation="lighter";
      x.lineCap="round";
      for(var j=0;j<N;j++){
        var k=j/(N-1);
        var breathe=reduce?0:0.035*Math.sin(t*0.42+k*5.2);
        var rx=RX*(0.70+0.32*k)*(1+breathe);
        var ry=RY*(0.50+0.62*k)*(1+breathe*1.6);
        var rot=(k-0.5)*0.085+(reduce?0:0.032*Math.sin(t*0.3+k*3.1));
        var band=Math.pow(Math.sin(Math.PI*Math.min(1,Math.max(0,k))),1.5);
        var shim=reduce?1:(0.72+0.28*Math.sin(t*0.85+k*9.4));
        var al=(0.030+(hero?0.165:0.095)*band)*shim*A.arc;
        var col=ramp(Math.min(1,k*0.92+0.06));
        x.strokeStyle="rgba("+col[0]+","+col[1]+","+col[2]+","+al.toFixed(4)+")";
        x.lineWidth=0.7+1.5*band;
        x.beginPath();
        x.ellipse(cx,cy,rx,ry,rot,Math.PI+0.10,-0.10,false);
        x.stroke();
      }

      var pulse=(reduce?0.42:(0.38+0.14*Math.sin(t*0.7)))*A.arc*(hero?1:0.50);
      x.strokeStyle="rgba(214,226,245,"+pulse.toFixed(3)+")";
      x.lineWidth=1.05;
      x.beginPath();
      x.ellipse(cx,cy,RX*0.905,RY*0.955,(reduce?0:0.02*Math.sin(t*0.3)),Math.PI+0.13,-0.13,false);
      x.stroke();

      if(!reduce){
        /* Glints travel along the crown. The sweep ADDS to the start angle:
           subtracting walks the bottom half of the ellipse and strands them
           below the figure as unexplained smudges. */
        for(var g=0; g<(hero?3:2); g++){
          var u=((t*0.085+g*0.41)%1);
          var ang=Math.PI+0.13+(Math.PI-0.26)*u;
          var gx=cx+Math.cos(ang)*RX*0.905, gy=cy+Math.sin(ang)*RY*0.955;
          var fade=Math.sin(Math.PI*u), rad=hero?22:17;
          var rg=x.createRadialGradient(gx,gy,0,gx,gy,rad);
          rg.addColorStop(0,"rgba(226,235,250,"+(0.5*fade*A.arc).toFixed(3)+")");
          rg.addColorStop(1,"rgba(226,235,250,0)");
          x.fillStyle=rg;
          x.beginPath(); x.arc(gx,gy,rad,0,6.2832); x.fill();
        }
      }
      x.globalCompositeOperation="source-over";
    }
    return {resize:resize,draw:draw};
  }

  var figs=[]; for(var i=0;i<nodes.length;i++){ var f=make(nodes[i]); if(f) figs.push(f); }
  if(!figs.length) return;

  var raf=null;
  function all(fn){ for(var i=0;i<figs.length;i++) figs[i][fn](arguments[1]); }
  function loop(ts){ for(var i=0;i<figs.length;i++) figs[i].draw(ts); raf=requestAnimationFrame(loop); }
  function once(){ for(var i=0;i<figs.length;i++) figs[i].draw(0); }
  function start(){
    all("resize");
    if(raf){ cancelAnimationFrame(raf); raf=null; }
    if(reduce) once(); else raf=requestAnimationFrame(loop);
  }

  /* A hidden tab still fires rAF in some browsers; stop burning frames. */
  document.addEventListener("visibilitychange",function(){
    if(document.hidden){ if(raf){ cancelAnimationFrame(raf); raf=null; } }
    else if(!reduce && !raf){ raf=requestAnimationFrame(loop); }
  });
  window.addEventListener("themechange",function(){ readTokens(); if(reduce) once(); });
  var rt=null;
  window.addEventListener("resize",function(){
    clearTimeout(rt); rt=setTimeout(function(){ all("resize"); if(reduce) once(); },120);
  });
  start();
})();
