"""Project OB timestamps from the exact exported snapshot without rounding them."""


def project_ob_times(payload: str) -> str:
    """Preserve block order/key matching and every BTS field for Pine's parser.

    Blocks with other fields but no BTS remain eligible for exact ticker matching;
    their explicit zero has the same numeric result as Pine's missing BTS. Blocks
    without fields are ignored by the existing Pine block selector as well.
    """
    projected = []
    for block in payload.split(';'):
        fields = block.split('~')
        if len(fields) < 2:
            continue
        timestamps = [field for field in fields[1:] if field.startswith('BTS:')]
        projected.append('~'.join([fields[0], *(timestamps or ['BTS:0'])]))
    return ';'.join(projected)
