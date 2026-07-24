# SMS Notification Consent

This document describes SMS opt-in for Beat the Streak's automated notification feature,
provided for Twilio toll-free verification review.

## Sole subscriber

Nicholas Carsner, the operator and owner of both the sending Twilio account and the
receiving phone number. This is a personal-use tool with no public subscriber base.

## Consent

By configuring and running this tool with my own Twilio credentials and phone number (set
via the `SUBSCRIBER_PHONE_NUMBER` environment variable), I consent to receiving automated
SMS messages containing MLB hit-probability picks, sent on a scheduled basis while the tool
is active (`SEASON_ACTIVE=true`).

## Message frequency

Up to several messages per day during MLB regular season — one message per distinct
game-start-time grouping with qualifying picks.

## Opt-out

Standard carrier-level STOP/HELP keyword handling applies via Twilio's built-in compliance
features on the verified toll-free number. Replying STOP to any message opts out at the
account level.
