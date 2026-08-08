# Probability Considerations & Model Roadmap

> Goal: Daily hit probability model for MLB hitters scheduled to play. Predict likelihood of recording >= 1 base hit in scheduled game using composite of historical (season) + trending (recent) matchup calculations via binomial distribution & Bayesian weighting.

Data sources:
- MLB Stats API
- Baseball Savant / Statcast
- Weather / Ballpark metrics

---

## Mathematical Foundation

- **Projected Plate Appearances ($PA_{proj}$)**: Lineup spot, home/away status, team expected scoring.
- **Single-PA Hit Probability ($p_{hit}$)**: Composite weighted sum of active calculators.
- **Game Hit Probability ($P(\ge 1\text{ Hit})$)**: 
  $$P(\ge 1\text{ Hit}) = 1 - (1 - p_{hit})^{PA_{proj}}$$

---

## Master List of Probability Calculators (76 Considerations)

### Category 1: Direct Pitcher vs. Batter (BvP) Matchups
1. `CALC_01` **BvP Career Hit Rate**: H / PA in all career head-to-head matchups.
2. `CALC_02` **BvP Season Hit Rate**: H / PA in current season head-to-head matchups.
3. `CALC_03` **BvP Recent Window Hit Rate**: H / PA in last 3 calendar years against pitcher.
4. `CALC_04` **BvP Contact Rate**: (PA - SO - BB) / PA against starting pitcher.
5. `CALC_05` **BvP Hard Hit %**: Batted balls >= 95 mph EV against starting pitcher.
6. `CALC_06` **BvP xBA / xwOBA**: Expected metrics on contact against starting pitcher.
7. `CALC_07` **BvP Whiff Rate**: Swings and misses / total swings against pitcher.
8. `CALC_08` **BvP Putaway Rate**: SO % in 2-strike counts against starting pitcher.

### Category 2: Platoon & Handedness Splits
9. `CALC_09` **Hitter Season vs. Pitcher Throws**: Hitter BA/xBA vs. pitch hand (LHP/RHP).
10. `CALC_10` **Hitter Recent (14d) vs. Pitcher Throws**: 14-day trending hit rate vs. pitch hand.
11. `CALC_11` **Pitcher Season vs. Batter Bats**: Pitcher BA/xBA allowed vs. batter hand (LHB/RHB).
12. `CALC_12` **Pitcher Recent (14d) vs. Batter Bats**: 14-day trending BA allowed vs. batter hand.
13. `CALC_13` **Switch-Hitter Split Acuity**: Hit rate differential for switch hitter from current side.
14. `CALC_14` **Arm Slot / Release Angle Match**: Hitter BA vs. pitcher arm angle (Overhand, 3/4, Sidearm).
15. `CALC_15` **Reverse Platoon Split Index**: Penalties/boosts for non-traditional split hitters/pitchers.

### Category 3: Pitch Arsenal & Movement Matchups
16. `CALC_16` **Primary Fastball xBA Match**: Hitter xBA vs pitch type x Pitcher primary fastball usage %.
17. `CALC_17` **Breaking Ball xBA Match**: Hitter xBA vs Slider/Sweeper/Curve x Pitcher breaking usage %.
18. `CALC_18` **Offspeed xBA Match**: Hitter xBA vs Changeup/Splitter x Pitcher offspeed usage %.
19. `CALC_19` **Run Value / 100 Match**: Hitter RV/100 on pitcher's top-2 most frequent pitch types.
20. `CALC_20` **Velocity Tier Match**: Hitter hit rate vs pitch velocity bracket (<92 mph, 92-96 mph, >96 mph).
21. `CALC_21` **Vertical Approach Angle (VAA) Match**: Hitter swing plane vs. pitcher VAA / top-zone rise.
22. `CALC_22` **Horizontal Break Acuity**: Hitter hit rate vs. extreme sweeper/sinker horizontal movement.
23. `CALC_23` **Pitch Extension Match**: Pitcher release extension impact on perceived velocity vs. hitter react time.

### Category 4: Plate Discipline & Zone Location Profiles
24. `CALC_24` **Zone Contact vs. Zone pitch %**: Hitter Z-Contact % x Pitcher Zone %.
25. `CALC_25` **Chase Vulnerability**: Hitter Chase (O-Swing) % x Pitcher O-Zone pitch %.
26. `CALC_26` **Whiff Overlay**: Hitter overall Whiff % x Pitcher overall Swing-and-Miss %.
27. `CALC_27` **CSW% Interaction**: Hitter Called Strike + Whiff rate vs pitcher CSW %.
28. `CALC_28` **First-Pitch Attack**: Hitter 0-0 Swing % x Pitcher First-Pitch Strike (F-Strike) %.
29. `CALC_29` **2-Strike Protection**: Hitter 2-strike Contact % vs pitcher 2-strike Out %.
30. `CALC_30` **Quadrant Location Acuity**: Hitter xBA in 3x3 zone quadrants vs pitcher pitch location heatmap.

### Category 5: Ballpark & Environmental Conditions
31. `CALC_31` **Ballpark Overall Hit Factor**: 3-year park factor for base hits (1B/2B/3B).
32. `CALC_32` **Ballpark Handedness Hit Factor**: Park factor specific to LHB/RHB.
33. `CALC_33` **Ballpark Wall/Field Geometry Match**: Spray angle of hitter vs. stadium fence distance/height.
34. `CALC_34` **Temperature Buckets**: Temperature modifier (<60°F down, 60-75°F neutral, 75-90°F up, >90°F high up).
35. `CALC_35` **Air Density / Humidity / Altitude**: Density Altitude Index (Coors, humid summer air boost).
36. `CALC_36` **Wind Speed & Direction**: Vector calculation (wind blowing out/in/crossfield vs spray angle).
37. `CALC_37` **Day / Night Performance**: Hitter & Pitcher splits in Day vs Night starts.
38. `CALC_38` **Home / Away Split**: Hitter Home vs Road BA & Pitcher Home vs Road BA allowed.
39. `CALC_39` **Dome / Roof Status**: Retractable roof open vs closed hit probability delta.
40. `CALC_40` **Venue Familiarity**: Hitter historical hit rate at current visiting venue.

### Category 6: Lineup & Game Context Factors
41. `CALC_41` **Lineup Spot PA Expectation**: Projected PAs based on spot 1-9 (1=4.6 PA down to 9=3.7 PA).
42. `CALC_42` **Preceding Batter OBP Impact**: Top-of-lineup OBP -> pitcher forced into stretch / more PAs.
43. `CALC_43` **Succeeding Batter Protection**: Threat of on-deck hitter -> pitcher strikes vs walks.
44. `CALC_44` **Times Through Order (TTO) Vulnerability**: Pitcher 1st, 2nd, 3rd TTO BA allowed vs hitter TTO performance.
45. `CALC_45` **Home Team Bottom 9th PA Risk**: Home hitters lose 0.5 PA if team leading in 9th inning.
46. `CALC_46` **Expected Game Pace / Scoring Total**: Vegas game run total as proxy for PA / hitting environment.

### Category 7: Opposing Bullpen Exposure
47. `CALC_47` **Bullpen Season BA Allowed**: Aggregate opponent bullpen hit allowance.
48. `CALC_48` **Bullpen Recent (7d) Fatigue Index**: High-leverage reliever usage in last 3 days -> mop-up pitchers.
49. `CALC_49` **Expected Reliever Handedness Mix**: Projected 6th-9th inning bullpen handedness vs hitter.
50. `CALC_50` **Bullpen Contact Rate**: Opposing bullpen In-Play % / Whiff %.
51. `CALC_51` **Opener / Bulk Reliever Adjustment**: Game starts with opener -> matchup shift for 2nd/3rd PA.

### Category 8: Batter Trending Form & Quality of Contact
52. `CALC_52` **3-Game Hit Rate**: Hit recorded in last 3 games (Y/N, multi-hit frequency).
53. `CALC_53` **7-Game Hit Rate**: BA / hit frequency in last 7 calendar days.
54. `CALC_54` **14-Game Trending BA**: BA in last 14 calendar days.
55. `CALC_55` **14-Game xwOBA / Hard-Hit Trend**: Luck-neutralized contact quality over last 14 days.
56. `CALC_56` **Active Hit Streak Length**: Current unbroken games with >=1 hit.
57. `CALC_57` **Slump Indicator / Luck Delta**: BA minus xBA delta (high xBA + low BA = positive regression candidate).
58. `CALC_58` **BABIP Regression Vector**: Recent BABIP vs season/career BABIP baseline.
59. `CALC_59` **Sweet Spot % Trend**: Launch angle 8°-32° contact rate over last 30 PAs.

### Category 9: Pitcher Trending Form & Fatigue
60. `CALC_60` **Pitcher Last 2 Starts Game Score**: Short-term performance trend.
61. `CALC_61` **Pitcher Last 3 Starts Hits/9 & WHIP**: Short-term hit allowance trend.
62. `CALC_62` **Pitcher Velocity Delta**: Fastball velocity in last start vs season average (loss of 1.5+ mph = hit boost).
63. `CALC_63` **Pitcher Command / Walk Delta**: Recent Zone % drop -> middle-middle mistake pitch rate.
64. `CALC_64` **Pitcher Rest Days**: Days rest (3d short, 4-5d normal, 6+d long rest split).
65. `CALC_65` **Pitcher Recent Hard-Hit Allowed %**: Hard contact surrendered over last 2 starts.

### Category 10: Defense, Umpires & Schedule Fatigue
66. `CALC_66` **Opposing Infield Defense OAA**: Out Above Average for pitcher's IF (groundball hit likelihood).
67. `CALC_67` **Opposing Outfield Defense OAA**: Out Above Average for pitcher's OF (flyball/line drive hit likelihood).
68. `CALC_68` **Umpire Strike Zone Size**: Umpire CSW % & strike zone size index (wide zone = lower hit rate).
69. `CALC_69` **Day-After-Night Game Fatigue**: Night game previous day + day game today -> hit rate penalty.
70. `CALC_70` **Travel / Timezone Displacement**: Cross-country travel without off-day penalty.
71. `CALC_71` **Unfamiliarity Factor**: Pitcher vs batter 0 career PAs (pitcher advantage 1st TTO).

### Category 11: Composite Model Aggregation & Probability Outputs
72. `CALC_72` **Empirical Season Base Rate Prob**: Hit/PA from full season data.
73. `CALC_73` **Trending Short-Window Prob**: Hit/PA from 14-day weighted window.
74. `CALC_74` **Matchup Specific Prob**: Hit/PA conditioned on BvP + Pitch Type + Platoon.
75. `CALC_75` **Bayesian Composite Hit/PA**: Weighted posterior blending CALC_72, 73, 74.
76. `CALC_76` **Final Game Hit Probability**: $P(\ge 1\text{ Hit}) = 1 - (1 - \text{Bayesian Composite})^{PA_{proj}}$.

---

## Sample Batters Test Suite

List of batters for baseline model benchmarking:
- Bryce Harper
- Yordan Alvarez
- Freddie Freeman
- Fernando Tatis Jr.
- Mike Trout
- Alec Burleson
- James Wood
- Ernie Clement
- Randy Arozarena
- Jake Mangum
- Matt Olson
- Ketel Marte
- Jacob Wilson