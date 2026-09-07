# Phone script - {state} {sale_type_label}
Case {case_id}: {owner_name}, {property_address} ({county} County)
Amount held: {surplus_display} | Held by: {holder}
{channel_warning}

## Before dialing
- Letter mailed on: ______ (only call after a letter has gone out)
- Number checked against the National Do Not Call Registry: [ ]
- Best number and source: {best_phone} ({best_phone_source})
- No autodialer, no prerecorded message, no text without written consent.

## Opening (identify yourself in the first sentence)
"Hi, is this {recipient_name}? My name is {signer_name} with {business_name}. We're a private company, not the court or the county. I sent you a letter about money the {holder} is holding from the {sale_kind} of {property_address}. Is this a good time for two minutes?"

If no: "No problem. Is there a better time, or would you rather I just mail the details?" Log and end.

## Verify (never share the amount until you have confirmed identity)
- "Did you own the property at {property_address} around {sale_date_long}?"
- "Is your mailing address still {recipient_address}?"

## Explain
"When the property sold on {sale_date_long}, it sold for more than what was owed. The {holder} is holding about {surplus_display} that may belong to you. You can claim it yourself for free by contacting the {holder} about {claim_reference_label} {case_number}. {fee_sentence}"

## Handle questions
- "Is this a scam?" - "Fair question. Don't send us anything. Call the {holder} at the number on their website and ask about {case_number}. When you're satisfied, call me back."
- "How much will I get?" - "The {holder} pays valid liens first, so the final number can be lower. We only earn a fee on what you actually receive."
- "Why should I use you?" - "You don't have to. We handle the forms, notarization, and follow-up, and we only get paid if you get paid."

## Close
- Interested: confirm mailing address and email, send the agreement packet. Next action: ______
- Not interested: "Understood. I'll note that and you won't hear from us again." Mark case closed.
- Wants to think: schedule a follow-up date. Do not call more than twice in a 30-day period.

## Log after the call
Outcome: ______ Date/time: ______ Notes: ______
