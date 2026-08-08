# psuedocode for guided character creation 

START CHARACTER CREATION

    load editable character sheet

    character = new Character()

    STEP 1: CONCEPT

        display:
            "Character Concept"

            "Your character concept is a 3-5 word sentence
             that describes what your character is at their core.
             We'll keep this handy for you so that you can keep
             it in mind while you make other decisions."

        display text input

        wait for user to enter concept

        if concept is empty:
            show "Please enter a character concept."
            remain on STEP 1

        else:
            character.concept = user_input

            pdf.set_field(
                "concept",
                character.concept
            )

            proceed to STEP 2


    STEP 2: VIRTUE AND VICE

        display:
            "Virtue and Vice"

            "Your Virtue and Vice represent the two forces
             that most strongly shape your character's behavior.
             Choose one Virtue and one Vice."

        display VICE choices on left:

            PRIDE
                "You value your own accomplishments and
                 believe you deserve recognition."

            GREED
                "You are driven by the desire to acquire
                 and possess more."

            LUST
                "You are driven by desire and seek
                 gratification through passion."

            ENVY
                "You resent what others have and desire
                 what belongs to them."

            GLUTTONY
                "You indulge your appetites and have
                 difficulty denying yourself pleasure."

            WRATH
                "You respond to problems with anger,
                 aggression, or violence."

            SLOTH
                "You avoid effort and responsibility,
                 preferring the path of least resistance."


        display VIRTUE choices on right:

            FAITH
                "You place your trust in something greater
                 than yourself."

            HOPE
                "You believe that things can become better,
                 even in difficult circumstances."

            CHARITY
                "You are motivated by compassion and a
                 desire to help others."

            FORTITUDE
                "You endure hardship and refuse to give up
                 when things become difficult."

            JUSTICE
                "You believe people should be treated fairly
                 and that wrongs should be corrected."

            PRUDENCE
                "You think carefully before acting and
                 consider the consequences of your choices."

            TEMPERANCE
                "You exercise restraint and seek balance
                 rather than excess."


        user selects one VICE

            character.vice = selected_vice

            pdf.set_field(
                "Vice",
                character.vice
            )

        user selects one VIRTUE

            character.virtue = selected_virtue

            pdf.set_field(
                "Virtue",
                character.virtue
            )


        when both have been selected:

            display:
                selected Virtue
                selected Vice
                character concept

            enable "Continue"

        when user selects Continue:

            proceed to STEP 3

    STEP 3: ATTRIBUTE PRIORITIES

    display:

        "Choose Your Attribute Priorities"

        "Think about your character concept.
         Which of these are they best at?"

    display three buttons:

        POWER
            "Your character's ability to impose their will
             on the world through physical or social force."

        FINESSE
            "Your character's ability to act with speed,
             precision, awareness, and subtlety."

        RESISTANCE
            "Your character's ability to endure, withstand,
             and resist what the world throws at them."


    user selects PRIMARY category

        character.attributes.primary = selected_category


    display:

        "Now, which are they worst at?"

    display the same three buttons,
    excluding the primary selection


    user selects TERTIARY category

        character.attributes.tertiary = selected_category

        remaining category becomes SECONDARY


    assign point values:

        primary   = 5
        secondary = 4
        tertiary  = 3

    store:

        character.attributes.power.points
        character.attributes.finesse.points
        character.attributes.resistance.points

    proceed to STEP 4

    Player chooses:
    Best  → Power
    Worst → Resistance

    for example:
        Application stores:
            Power      = 5
            Finesse    = 4
            Resistance = 3


    STEP 4: POWER

    points_available = character.attributes.power.points

    display:

        "Power"

        "You have {points_available} points to spend
         among Intelligence, Strength, and Presence."

        tooltip:
            "The first three dots in an Attribute cost
             one point each. Increasing an Attribute to
             four dots costs an additional point."

    display three attribute controls:

        Intelligence    [ - ]  ● ● ● ● ●  [ + ]
        Strength        [ - ]  ● ● ● ● ●  [ + ]
        Presence        [ - ]  ● ● ● ● ●  [ + ]

    starting dots:
        1 in each attribute

    calculate cost:

        cost(attribute):

            if dots <= 3:
                return dots - 1

            if dots == 4:
                return 4

            if dots == 5:
                return 6


    available_points =
        points_available
        - cost(Intelligence)
        - cost(Strength)
        - cost(Presence)

    prevent user from spending more than points_available

    when finished:

        character.attributes.intelligence = selected value
        character.attributes.strength = selected value
        character.attributes.presence = selected value

    proceed to STEP 5

    STEP 5: FINESSE

    points_available =
        character.attributes.finesse.points

    attributes:
        Dexterity
        Wits
        Manipulation

    spend points

    save results


STEP 6: RESISTANCE

    points_available =
        character.attributes.resistance.points

    attributes:
        Stamina
        Resolve
        Composure

    spend points

    save results

    We then repeat this Attribute Category method we just defined, but for SKILL categories instead. The skill categories are Mental, Physical, and Social. They can assign 11/7/4. The logic is identical. So we don't need to go over it again.

    Skills are:
    Mental
    (-3 unskilled)
        Academics__________
        Computer__________
        Crafts_____________
        Investigation_________
        Medicine___________
        Occult_____________
        Politics____________
        Science _____________
        Athletics __________
        Brawl___________
        Drive_____________
    Physical
    (-1 unskilled)
        Firearms_________
        Larceny___________
        Stealth_____________
        Survival____________
        Weaponry___________
        Animal Ken________
        Empathy_________
        Expression_________
    Social
    (-1 unskilled)
        Intimidation_________
        Persuasion___________
        Socialize___________
        Streetwise__________
        Subterfuge_______